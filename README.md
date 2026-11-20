# concord-416 - shared closing dashboard (external)

Public page: https://hamhockhq.github.io/concord-416/
API Worker: https://concord-416-api.howardmd1.workers.dev
Source of truth for deal data: the CLOSING_PROCESS closing files
(DASHBOARD_DATA.json + the Muse tracker). This repo holds no deal data;
the static page renders only what the session-gated Worker returns.

## Layout

- `index.html` - the Pages front end (V29 style). Contains no deal data.
- `worker/worker.js` - Cloudflare Worker + Durable Object (SQLite):
  magic-link auth, per-person payload slicing, admin endpoints.
- `tools/deploy_api.py` - deploy/redeploy the Worker (`deploy`, `smoke`).
- `tools/seed_people.py` - idempotently seed the people roster.
- `tools/publish_dashboard.py` - build the ALLOWLIST payload and publish it.
- `tools/verify_functional.py` - builder functional checklist (live Worker).
- `tools/capture_shots.js` - screenshots + axe-core run.
- `data/tracker_snapshot.json` - tracker capture used by the publish step.

## Auth model (per RESEARCH.md)

- Opaque 32-byte sign-in tokens; only the SHA-256 hash is stored.
- 15-minute token expiry; atomic single-use redeem inside the Durable Object.
- The emailed link lands on a Worker confirm page; GET spends nothing
  (mail scanners are harmless). One tap on "Sign in as [first name]" redeems.
- A new link invalidates the person's older unused links. Link generation
  is capped at 5 per person per hour, except Matt (owner, tag "Matt"), who is
  whitelisted at his instruction on 2026-11-20.
- Sessions: 7 days for Matt, 24 hours for everyone else (config constants
  in worker.js: SESSION_TTL_MS). Auto-shutoff dates live in the same config
  (SHUTOFF) and are currently unset.
- The Pages page receives the session credential in the URL fragment after
  redeem, stores it in sessionStorage, and sends it as a Bearer header.
  (The Worker also sets an HttpOnly cookie on its own origin.)
- Disabling a person (admin) deletes their unused tokens and revokes
  their sessions in the same call.

## Who sees what

Every record carries a personTag. A signed-in person sees records tagged
for them plus records tagged "All parties". Matt (tag "Matt") is the only
full view. Money-panel figures and Matt's to-dos are never published to
the Worker (the publish step builds from an allowlist and refuses to run
if a money marker appears in the payload).

Two slicing rules from Matt's 2026-11-20 walkthrough:

- Carole (tag "Carole") sees the FULL step timeline, not only her tagged
  steps: she is the one keeping the buyer on track, so her slice carries
  every step (deposit, lender decision, inspection, application,
  appraisal, commitment, settlement). Her documents, open items and
  flags stay sliced as usual. Longer term: a per-person visibility
  filter Matt controls, instead of this hard-coded exception.
- Lender names stay out of other people's views. In any non-Matt slice,
  a lender's organisation appears only on that lender's own row of the
  people list; shared step/document text uses neutral wording ("the
  lender named in the signed agreement"), so one lender's view never
  names the competing lender or the contract lender. The client-side
  Preview as in index.html mirrors both rules; keep the two in step.

## Open items

- Secure per-person document hosting (noted 2026-11-20, not urgent):
  documents are currently one tap away for everyone who can see them in
  their slice. Long term they need a secure host with per-person access
  control (Cloudflare or GitHub, where the dashboard already lives)
  instead of shared Drive links. Fine for now; not built.

## Publishing updates (the sync step)

When the closing files change, refresh the dashboard:

    python3 tools/publish_dashboard.py            # build + guard + POST /admin/publish
    python3 tools/publish_dashboard.py --dry-run  # build + guard only

The payload is built from `data/tracker_snapshot.json` (refresh it from
the tracker when steps/documents/open items change) plus the allowlisted
deadline fields of DASHBOARD_DATA.json. The drift-check job adopts this
step later; until then it is run by hand after closing-file updates.

Admin token: `/tmp/concord-416/token` (0600, created by deploy_api.py).
It is the Worker's PUBLISH_TOKEN secret. /tmp does not survive a service
restart; if the file is gone, run `python3 tools/deploy_api.py deploy`
once - it regenerates the token and re-syncs the Worker secret.

## Admin operations (Bearer = admin token)

- POST /admin/people - list roster
- POST /admin/seed {people:[...]} - upsert roster rows by name
- POST /admin/person {id, enabled?, chosen?} - enable/disable/mark chosen
- POST /admin/link {personId} - generate a sign-in link (TEST/ops use;
  sending links to people is a separate, gated step)
- POST /admin/publish {payload} - replace the published payload
- POST /admin/requests - list queued sign-in requests from the roster form
- POST /admin/state - audit view (hashes and counts only)
- POST /admin/expire {personId} - test hook: expire a person's latest link

## Send hold

No sign-in links or emails are sent from this tooling. Link delivery is
Phase 1 manual (Gmail, one person at a time, Matt's yes per batch) until
Matt lifts the hold after independent verification and his own look-over.
