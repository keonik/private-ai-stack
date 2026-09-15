# deployer — push to deploy, on this Mac

The part of Coolify that fits on one machine. A launchd agent checks each app's git branch every minute;
when it moves, it builds the app's Dockerfile, starts the new image **beside** the live one, and replaces
the live container only once the new one passes its health check. It sends a phone notification either
way through the stack's ntfy topic.

```
git push  ──►  (≤ 60 s)  git ls-remote sees a new commit
                         │  nothing under "watch" changed? record it, no rebuild
                         ▼
                 docker build  ──fail──►  notify, keep serving the old version
                         ▼
          run beside the live one on a spare port
                         │  health check  ──fail──►  notify, keep serving the old version
                         ▼
     replace the live container on its port (~1 s gap)
                         │  health check  ──fail──►  roll back to the previous image, notify
                         ▼
               notify "deployed", keep the last 3 images for rollback
```

## Why not Coolify, a webhook, or a CI runner

- **Coolify** manages Linux servers over SSH; it cannot drive Docker on macOS.
- **Webhooks** need something inbound — a public endpoint to secure and GitHub settings per repo. Polling
  needs nothing, and a `git ls-remote` a minute costs nothing. The price is up to a minute of delay.
- **A self-hosted GitHub Actions runner** works too, but ties deploys to GitHub and runs arbitrary workflow
  code from the repo on this machine. This does one thing: build the Dockerfile at a known commit.

## Add an app

```bash
cp stack/deployer/apps/example.json ~/.config/deployer/apps/myapp.json   # edit it
$EDITOR ~/.config/deployer/env/myapp.env                                 # secrets, chmod 600
python3 stack/deployer/deployer.py deploy myapp                          # first deploy now
```

Then point a Cloudflare tunnel public hostname at `http://localhost:<publish>`. From then on, pushing to the
branch deploys it.

| field | meaning |
|---|---|
| `repo`, `branch` | what to watch; private repos use git's credential helper (`gh auth setup-git`) |
| `context`, `dockerfile` | build context inside the repo, and the Dockerfile relative to it |
| `port` / `publish` | the container's port / the host port on 127.0.0.1 the tunnel points at |
| `health`, `health_timeout` | a path that must answer 200 before the new version takes over |
| `env_file` | `--env-file` for the container; lives in `~/.config/deployer/env/`, never in a repo |
| `watch` | only commits touching these paths rebuild — a monorepo push elsewhere is recorded, not deployed |
| `docker_args` | anything extra for `docker run` |

`~/.config/deployer/config.json` holds `ntfy_url`. Containers carry the compose service label, so Alloy
ships their logs to Loki like every other container.

```bash
python3 stack/deployer/deployer.py status            # what runs where, at which commit, last failure
python3 stack/deployer/deployer.py deploy myapp v1.2 # a branch, tag or commit, now
python3 stack/deployer/deployer.py rollback myapp    # previous image; that commit is not retried
python3 stack/deployer/deployer.py logs myapp
tail -f ~/Library/Logs/private-ai-stack.deployer.log
```

A commit that failed, or that was rolled back, is pinned and not retried every minute — the next push moves
past it. A container that disappears (the Docker VM was reset) is started again from its image on the next
tick.

**Known limits.** The swap is stop-then-start, so there is a ~1 second gap; zero downtime would need a proxy
in front that switches upstreams. Published ports go through colima's port forwarding, which can die
silently — the same failure mode as every other container on this machine.
