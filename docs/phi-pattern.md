# PHI-aware pattern (HIPAA-oriented, not legal advice)

What changes when the documents contain protected health information — or any
regulated data (attorney–client, financial). This is the checklist buyers in
healthcare, legal, and finance ask for by name.

## 1. Where the data lives decides everything

| Deployment | Who is a Business Associate | BAA needed |
|---|---|---|
| Box in the covered entity's office, they own it | Nobody new | No |
| Your server, their data transits it | You | Yes, with them |
| Cloud provider you control | You **and** the provider | Yes, both |

**The on-prem appliance is the simplest compliance story:** nothing leaves the
building, no new business associate exists. That is the whole reason this stack
supports running on a Mac mini.

If you host it: use a provider that signs a BAA (AWS, GCP, Azure do), and sign
one with each customer. Budget weeks, not days.

## 2. Minimization

- Don't store what you don't need. Raw audio, once transcribed and confirmed, is
  a liability; give it a retention window and delete on schedule.
- Strip identifiers from anything that leaves the trust boundary. If a step must
  call an external model, de-identify first (name, DOB, MRN, addresses, dates
  shifted), and re-identify locally. Prefer never leaving the boundary.
- Chunk-level access: retrieval should only return chunks the requesting user is
  allowed to see. Tag chunks with an ACL at ingest; filter at query.

## 3. Audit

- Every model call: who (key), when, model, prompt hash, tokens, cost. LiteLLM
  writes this to Postgres out of the box.
- Every retrieval: who, query hash, which document ids/pages were returned.
  `rag-ingest` writes `audit.jsonl`.
- Every admin action: key issued/revoked, document ingested/deleted.
- Logs are append-only and backed up with the data. Keep prompts out of logs
  unless you have a reason and a retention policy; hash them.

## 4. Access control

- One virtual key per human and per app. No shared keys.
- Budgets and rate limits per key (LiteLLM `max_budget`, `rpm_limit`).
- Open WebUI: signup off after first admin; SSO if they have it.
- Postgres and LanceDB are not on the host network. Only the UI and, if
  needed, the API port are exposed, behind TLS.

## 5. Encryption

- At rest: full-disk encryption on the box (FileVault on Mac, LUKS on Linux) is
  the floor. Volumes inherit it.
- In transit: TLS at the reverse proxy / tunnel. Internal Docker network is
  fine unencrypted on a single host; not across hosts.

## 6. Escalation and safety rules (clinical/legal use)

- The model **drafts**; a licensed human **signs**. The UI should make the
  review step unavoidable, not optional.
- Show sources for every claim. No citation → the answer says so.
- Refuse out-of-scope asks (diagnosis, legal advice to end clients) with a
  scripted handoff, not a hallucinated answer.
- Log the model version used for every note that gets signed.

## 7. Incident basics

- Know how to answer "which patients' data did this key touch" in one query.
  The audit schema above makes that a `WHERE api_key = …`.
- Key revocation is the kill switch; practice it.

## 8. What to hand the customer

- This document, adapted.
- A data-flow diagram (see `architecture.md`).
- Retention schedule.
- Backup/restore runbook with the last successful restore date.
