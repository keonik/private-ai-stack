#!/usr/bin/env python3
"""A small push-to-deploy loop for this Mac — the part of Coolify that fits on one machine.

Every minute (launchd) it asks each app's git remote for the head of its branch. When that moves it builds
the app's Dockerfile, starts the new image **beside** the old one on a spare port, and only when the new one
answers its health check does it replace the live container. A build or health failure leaves the old
version serving and sends a notification; a new version that fails after the swap is rolled back to the
previous image automatically.

Why polling and not webhooks: it needs nothing inbound — no public endpoint to secure, nothing to configure on
GitHub — and one `git ls-remote` a minute is free. The cost is up to a minute of delay after a push.

Why not Coolify here: it manages Linux servers over SSH and cannot drive Docker on macOS.

    deployer.py tick                 # what launchd runs: check every app, deploy what changed
    deployer.py status               # what is running, at which commit, and the last failure
    deployer.py deploy <app> [ref]   # deploy now, optionally a branch, tag or commit
    deployer.py rollback <app>       # back to the previous image
    deployer.py logs <app>           # follow the live container's logs

Apps are JSON files in ~/.config/deployer/apps/ (see apps/example.json); secrets live in the env file they
point at, never in the repo. Standard library only, and Python 3.9, so it runs on the system python.
"""
from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HOME = Path.home()
CONFIG = Path(os.environ.get("DEPLOYER_CONFIG", HOME / ".config/deployer"))
DATA = Path(os.environ.get("DEPLOYER_DATA", HOME / ".local/share/deployer"))
LOGS = HOME / "Library/Logs/deployer"
KEEP_IMAGES = 3


def log(msg: str) -> None:
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}", flush=True)


def run(cmd: list, check: bool = True, capture: bool = True, timeout: int = 1800, **kw) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=capture, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f"{' '.join(map(str, cmd[:4]))} failed: {(r.stderr or r.stdout or '').strip()[-600:]}")
    return r


def settings() -> dict:
    f = CONFIG / "config.json"
    return json.loads(f.read_text()) if f.exists() else {}


def notify(title: str, body: str, priority: str = "default") -> None:
    url = (settings().get("ntfy_url") or "").strip()
    log(f"{title}: {body}")
    if not url:
        return
    try:
        req = urllib.request.Request(url, data=body.encode(), method="POST",
                                     headers={"Title": title, "Priority": priority, "Tags": "rocket",
                                              "User-Agent": "deployer/1.0"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception as e:  # noqa: BLE001 — a missed notification must not fail a deploy
        log(f"notify failed: {e}")


class App:
    def __init__(self, path: Path):
        c = json.loads(path.read_text())
        self.name = path.stem
        self.repo = c["repo"]
        self.branch = c.get("branch", "main")
        self.context = c.get("context", ".").strip("/") or "."
        self.dockerfile = c.get("dockerfile", "Dockerfile")
        self.port = int(c["port"])                    # inside the container
        self.publish = int(c["publish"])              # on 127.0.0.1 — a tunnel or proxy points here
        self.health = c.get("health", "/")
        self.health_timeout = int(c.get("health_timeout", 90))
        self.env_file = Path(os.path.expanduser(c["env_file"])) if c.get("env_file") else None
        self.watch = [w.strip("/") for w in c.get("watch", [])]  # only these paths trigger a rebuild
        self.docker_args = list(c.get("docker_args", []))
        self.enabled = c.get("enabled", True)
        self.dir = DATA / self.name
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.dir / "state.json"

    @property
    def container(self) -> str:
        return f"deployer-{self.name}"

    def image(self, sha: str) -> str:
        return f"deployer/{self.name}:{sha[:12]}"

    def state(self) -> dict:
        return json.loads(self.state_file.read_text()) if self.state_file.exists() else {}

    def save(self, **changes) -> None:
        s = self.state()
        s.update(changes, updated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        self.state_file.write_text(json.dumps(s, indent=1))

    # ---- git ---------------------------------------------------------------------------------------------
    def remote_head(self, ref: str | None = None) -> str:
        ref = ref or self.branch
        if len(ref) >= 7 and all(ch in "0123456789abcdef" for ch in ref.lower()):
            self.fetch()
            return run(["git", "-C", str(self.mirror), "rev-parse", ref]).stdout.strip()
        # One blip on a home connection should not look like a failed deploy check, so it is tried twice.
        for attempt in (1, 2):
            try:
                out = run(["git", "ls-remote", self.repo, ref], timeout=120).stdout.split()
                break
            except (subprocess.TimeoutExpired, RuntimeError) as e:
                if attempt == 2:
                    raise
                log(f"{self.name}: git ls-remote {e.__class__.__name__}, retrying")
                time.sleep(5)
        if not out:
            raise RuntimeError(f"{self.repo} has no ref {ref}")
        return out[0]

    @property
    def mirror(self) -> Path:
        return self.dir / "repo.git"

    def fetch(self) -> None:
        if not self.mirror.exists():
            run(["git", "clone", "--bare", self.repo, str(self.mirror)], timeout=600)
        run(["git", "-C", str(self.mirror), "fetch", "--prune", "origin", "+refs/heads/*:refs/heads/*",
             "+refs/tags/*:refs/tags/*"], timeout=600)

    def changed_paths(self, old: str, new: str) -> list[str]:
        r = run(["git", "-C", str(self.mirror), "diff", "--name-only", old, new], check=False)
        return r.stdout.split() if r.returncode == 0 else ["<unknown>"]

    def export(self, sha: str) -> Path:
        """The build context at exactly this commit, without a working tree to drift."""
        out = self.dir / "build"
        run(["rm", "-rf", str(out)])
        out.mkdir()
        archive = subprocess.Popen(["git", "-C", str(self.mirror), "archive", sha, self.context], stdout=subprocess.PIPE)
        run(["tar", "-x", "-C", str(out)], stdin=archive.stdout)
        archive.wait()
        if archive.returncode != 0:
            raise RuntimeError(f"git archive {sha[:12]} {self.context} failed")
        return out / self.context

    # ---- docker ------------------------------------------------------------------------------------------
    def start(self, name: str, image: str, publish: int, restart: bool) -> None:
        run(["docker", "rm", "-f", name], check=False)
        cmd = ["docker", "run", "-d", "--name", name, "-p", f"127.0.0.1:{publish}:{self.port}",
               "--add-host", "host.docker.internal:host-gateway",
               # the same label compose sets, so Alloy ships these logs to Loki labelled by app
               "--label", f"com.docker.compose.service={self.name}", "--label", f"dev.private-ai-stack.deployer={self.name}"]
        if restart:
            cmd += ["--restart", "unless-stopped"]
        if self.env_file:
            cmd += ["--env-file", str(self.env_file)]
        run(cmd + self.docker_args + [image])

    def healthy(self, publish: int, timeout: int) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{publish}{self.health}", timeout=3) as r:
                    if r.status == 200:
                        return True
            except Exception:  # noqa: BLE001 — not up yet
                pass
            time.sleep(1)
        return False

    def tail(self, name: str, n: int = 40) -> str:
        r = run(["docker", "logs", "--tail", str(n), name], check=False)
        return (r.stdout + r.stderr).strip()[-2000:]

    # ---- the deploy ----------------------------------------------------------------------------------------
    def deploy(self, sha: str, reason: str) -> bool:
        short = sha[:12]
        LOGS.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        log(f"{self.name}: deploying {short} ({reason})")
        self.save(status="building", target=sha)
        try:
            ctx = self.export(sha)
            build_log = LOGS / f"{self.name}-{short}.log"
            with build_log.open("w") as f:
                r = subprocess.run(["docker", "build", "-t", self.image(sha), "-f", str(ctx / self.dockerfile), str(ctx)],
                                   stdout=f, stderr=subprocess.STDOUT, timeout=1800)
            if r.returncode != 0:
                tail = build_log.read_text()[-800:]
                raise RuntimeError(f"build failed (log: {build_log})\n{tail}")
            built = time.time() - t0

            # Beside the live one first: a broken image never touches what is serving.
            candidate = f"{self.container}-candidate"
            spare = free_port()
            self.start(candidate, self.image(sha), spare, restart=False)
            if not self.healthy(spare, self.health_timeout):
                logs = self.tail(candidate)
                run(["docker", "rm", "-f", candidate], check=False)
                raise RuntimeError(f"new version never became healthy on {self.health}\n{logs}")
            run(["docker", "rm", "-f", candidate], check=False)

            previous = self.state().get("image")
            swap0 = time.time()
            self.start(self.container, self.image(sha), self.publish, restart=True)
            if not self.healthy(self.publish, 60):
                logs = self.tail(self.container)
                if previous:
                    self.start(self.container, previous, self.publish, restart=True)
                    self.healthy(self.publish, 60)
                raise RuntimeError(f"healthy beside the old one but not after the swap; rolled back to {previous}\n{logs}")
            gap = time.time() - swap0

            history = [i for i in [previous] + self.state().get("history", []) if i and i != self.image(sha)]
            self.save(status="running", deployed=sha, image=self.image(sha), history=history[:KEEP_IMAGES],
                      failed=None, last_error=None, build_seconds=round(built), swap_seconds=round(gap, 1))
            self.prune()
            notify(f"{self.name} deployed", f"{short} is live. Built in {built:.0f} s, swapped in {gap:.1f} s. ({reason})")
            return True
        except Exception as e:  # noqa: BLE001 — every failure is reported and recorded, never raised into launchd
            self.save(status="failed" if self.state().get("image") is None else "running", failed=sha,
                      last_error=str(e)[:1500])
            live = (self.state().get("deployed") or "nothing")[:12]
            notify(f"{self.name} deploy failed", f"{short}: {str(e)[:600]}\n\nStill serving {live}.", priority="high")
            return False

    def prune(self) -> None:
        keep = {self.state().get("image"), *self.state().get("history", [])}
        r = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}", f"deployer/{self.name}"], check=False)
        for img in r.stdout.split():
            if img not in keep:
                run(["docker", "rmi", img], check=False)

    def tick(self) -> None:
        if not self.enabled:
            return
        st = self.state()
        if st.get("image") and not container_running(self.container):
            # Deployed but gone (the Docker VM was reset, someone removed it): put the live image back.
            log(f"{self.name}: container missing, restarting {st['image']}")
            self.start(self.container, st["image"], self.publish, restart=True)
        head = self.remote_head()
        # `failed` pins a commit that failed or was rolled back, so it is not retried every minute; the next
        # push moves the head past it.
        if head == st.get("deployed") or head == st.get("failed"):
            return
        self.fetch()
        if st.get("deployed") and self.watch:
            touched = self.changed_paths(st["deployed"], head)
            if not any(p == w or p.startswith(w + "/") for p in touched for w in self.watch):
                log(f"{self.name}: {head[:12]} changes nothing under {self.watch}; recording without a rebuild")
                self.save(deployed=head)
                return
        self.deploy(head, "new commit on " + self.branch)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def container_running(name: str) -> bool:
    r = run(["docker", "inspect", "-f", "{{.State.Running}}", name], check=False)
    return r.stdout.strip() == "true"


def apps() -> dict[str, App]:
    d = CONFIG / "apps"
    return {p.stem: App(p) for p in sorted(d.glob("*.json"))} if d.exists() else {}


def main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else "tick"
    DATA.mkdir(parents=True, exist_ok=True)
    if cmd == "status":
        for name, a in apps().items():
            s = a.state()
            print(f"{name:16} {s.get('status', 'never deployed'):9} {(s.get('deployed') or '')[:12]:12} "
                  f"127.0.0.1:{a.publish}  {'running' if container_running(a.container) else 'NOT RUNNING'}  "
                  f"updated {s.get('updated_at', '-')}")
            if s.get("last_error"):
                print("   last failure:", s["last_error"].splitlines()[0])
        return 0
    if cmd == "logs":
        return subprocess.call(["docker", "logs", "-f", "--tail", "100", apps()[argv[2]].container])

    with open(DATA / ".lock", "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            log("another deployer run is in progress; skipping")
            return 0
        if run(["docker", "info"], check=False, timeout=30).returncode != 0:
            log("docker is not reachable; skipping this tick")
            return 0
        if cmd == "tick":
            for a in apps().values():
                try:
                    a.tick()
                except Exception as e:  # noqa: BLE001 — one broken app must not stop the others
                    log(f"{a.name}: tick failed: {e}")
            return 0
        a = apps()[argv[2]]
        if cmd == "deploy":
            ref = argv[3] if len(argv) > 3 else None
            a.fetch()
            return 0 if a.deploy(a.remote_head(ref), f"manual deploy of {ref or a.branch}") else 1
        if cmd == "rollback":
            history = a.state().get("history", [])
            if not history:
                print("no previous image to roll back to")
                return 1
            target = history[0]
            a.start(a.container, target, a.publish, restart=True)
            ok = a.healthy(a.publish, 60)
            bad = a.state().get("deployed")
            a.save(image=target, history=history[1:], deployed=None, failed=bad, status="running" if ok else "failed")
            notify(f"{a.name} rolled back", f"now running {target} ({'healthy' if ok else 'NOT healthy'})")
            return 0 if ok else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
