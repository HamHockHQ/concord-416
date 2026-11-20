#!/usr/bin/env python3
"""Publish the 416 dashboard payload to the concord-416-api Worker.

Builds an ALLOWLIST payload from:
  - ~/workspace/concord-416/data/tracker_snapshot.json (steps, documents,
    open items, people, deadlines captured from the Muse tracker; the
    drift-check job refreshes this snapshot from the closing record)
  - ~/workspace/house-416-concord/closing-process/DASHBOARD_DATA.json
    (only the allowlisted meta fields below are read)

The SHARED payload never contains money data: the `funds` section and
Matt's to-dos are excluded by construction (allowlist, not blocklist),
and a guard scan refuses to publish if a forbidden marker or any
currency amount appears in the shared (scrubbed) part of the payload.

MATT-ONLY LENDER SECTION (added 2026-11-20 at Matt's explicit request):
data/lender_data.json is attached as the top-level `lender` key AFTER
the shared part is scrubbed and guarded. It is never scrubbed and never
part of the guard's shared blob. Server-side, the Worker's sliceFor
returns the full payload only to Matt's session; every other person's
payload is constructed from an allowlist (meta, deadlines, people,
steps, documents, openItems, viewer) that has no `lender` key, so the
lender figures never leave the server for a non-Matt session. The
guard below fails the publish if any currency amount or lender figure
shows up in the shared part, which is the only part a non-Matt slice
can ever be built from.

Usage: publish_dashboard.py [--dry-run]
The admin token is read from /tmp/concord-416/token (never printed).
"""
import json, os, sys, urllib.request, urllib.error
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import checks as flag_checks

CONFLICTS = os.path.expanduser(
    "~/workspace/house-416-concord/closing-process/CONFLICTS.md")

SNAPSHOT = os.path.expanduser("~/workspace/concord-416/data/tracker_snapshot.json")
LENDER_DATA = os.path.expanduser("~/workspace/concord-416/data/lender_data.json")
DASHBOARD_DATA = os.path.expanduser(
    "~/workspace/house-416-concord/closing-process/DASHBOARD_DATA.json")
TOKEN_FILE = "/tmp/concord-416/token"
WORKER_URL = "https://concord-416-api.howardmd1.workers.dev"

# Extra people rows the external build adds on top of the tracker snapshot:
# Sam Totoro (second lender) and Sarah Tompos (title). personTag values here
# must match the tags used by steps/documents/openItems.
EXTRA_PEOPLE = [
    {"name": "Sam Totoro", "org": "Univest", "position": 7,
     "role": "Lender"},
    {"name": "Sarah Tompos", "org": "Homesale Settlement Services",
     "position": 8, "role": "Title"},
]

FORBIDDEN_MARKERS = [
    '"funds":', "liquid_for_sure", "boa_checking", "ending_balance",
    "balance_due", "cash_needed", "5192.28", "19900", "business_funds",
    "crypto", "getmoneypanel", "gettodos",
]

DOC_FIELDS = ["name", "detail", "status", "grp", "link", "note", "personTag", "position"]
STEP_FIELDS = ["title", "status", "owner", "dueNote", "note", "personTag", "position"]
ITEM_FIELDS = ["title", "issue", "path", "personTag", "position"]
PEOPLE_FIELDS = ["name", "org", "role", "position"]

def pick(record, fields):
    return {f: record.get(f) for f in fields}

def build_payload():
    snap = json.load(open(SNAPSHOT))
    dd = json.load(open(DASHBOARD_DATA))
    prop = dd.get("property", {})
    people = [pick(p, PEOPLE_FIELDS) for p in snap.get("people", [])]
    known = {p["name"] for p in people}
    for extra in EXTRA_PEOPLE:
        if extra["name"] not in known:
            people.append(extra)
    people.sort(key=lambda p: p.get("position") or 99)
    payload = {
        "meta": {
            "address": prop.get("address"),
            "executionDate": dd.get("execution_date"),
            "depositDueDate": dd.get("deposit_due_date"),
            "inspectionWindowEnd": dd.get("inspection_window_end"),
            "settlementDate": prop.get("settlement_date"),
            "commitmentDeadline": prop.get("commitment_deadline"),
            "lastUpdated": dd.get("last_updated"),
        },
        "deadlines": snap.get("deadlines", []),
        "steps": [pick(s, STEP_FIELDS) for s in snap.get("steps", [])],
        "documents": [pick(d, DOC_FIELDS) for d in snap.get("documents", [])],
        "openItems": [pick(i, ITEM_FIELDS) for i in snap.get("openItems", [])],
        "people": people,
    }
    return payload

import re

CURRENCY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
PAREN_CURRENCY_RE = re.compile(r"\s*\(\$[\d,]+(?:\.\d{2})?\)")

def scrub_text(s):
    # External payload carries NO money figures at all (Matt instruction:
    # money stays in his private tracker). Remove parenthetical amounts
    # whole, blank any remaining currency amounts, and tidy the seams.
    s = PAREN_CURRENCY_RE.sub("", s)
    s = CURRENCY_RE.sub("", s)
    s = re.sub(r"\bof\s+([.,]|$)", r"\1", s)
    s = re.sub(r"\s{2,}", " ", s)
    return s.strip()

def scrub(obj):
    if isinstance(obj, str):
        return scrub_text(obj)
    if isinstance(obj, list):
        return [scrub(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items()}
    return obj

# Distinctive lender-section markers and figures. None of these may
# appear in the shared part of the payload: the shared part is the only
# thing a non-Matt slice can be built from, so a hit here means lender
# data would be exposed to other people. The lender section itself is
# attached after this guard runs and is not scanned.
LENDER_MARKERS = [
    "cashToClose", "lifetimeInterest", "Lender breakdown",
    "2738.71", "17217.7", "14758.94", "299245", "463249",
]

def guard(payload):
    blob = json.dumps(payload)
    hits = [m for m in FORBIDDEN_MARKERS if m in blob]
    if hits:
        raise SystemExit(f"REFUSING TO PUBLISH: forbidden markers in payload: {hits}")
    lhits = [m for m in LENDER_MARKERS if m in blob]
    if lhits:
        raise SystemExit(f"REFUSING TO PUBLISH: lender markers in shared payload: {lhits}")
    # Hard rule added 2026-11-20 after the persona verification found
    # $308,500 and $3,000 in non-Matt slices: no currency amount may
    # survive into the external payload, in any field.
    cur = CURRENCY_RE.findall(blob)
    if cur:
        raise SystemExit(f"REFUSING TO PUBLISH: currency amounts in payload: {cur}")

def load_lender():
    with open(LENDER_DATA) as f:
        return json.load(f)

def main():
    dry = "--dry-run" in sys.argv
    # Flagger: deterministic cross-reference checks over the raw sources.
    # Shared flags are figure-free and join the shared payload before the
    # scrub/guard pass; Matt-only flags ride inside the lender section.
    with open(SNAPSHOT) as f:
        snap_raw = json.load(f)
    with open(DASHBOARD_DATA) as f:
        dd_raw = json.load(f)
    lender_pre = load_lender()
    conflicts_text = ""
    if os.path.exists(CONFLICTS):
        with open(CONFLICTS) as f:
            conflicts_text = f.read()
    flags_shared, flags_matt = flag_checks.build_flags(
        snap_raw, dd_raw, lender_pre, conflicts_text, date.today())
    payload = build_payload()
    payload["flags"] = flags_shared
    payload = scrub(payload)
    guard(payload)
    # Matt-only lender section: attached after the shared part has been
    # scrubbed and guarded, and verified to exist ONLY under the
    # top-level `lender` key, which non-Matt slices never receive.
    lender = load_lender()
    lender["flags"] = flags_matt
    def keys_of(obj, acc):
        if isinstance(obj, dict):
            for k, v in obj.items():
                acc.add(k); keys_of(v, acc)
        elif isinstance(obj, list):
            for x in obj:
                keys_of(x, acc)
        return acc
    bad_keys = keys_of(payload, set()) & {"lender", "cashToClose", "lifetimeInterest", "offers", "scenarios"}
    if bad_keys:
        raise SystemExit(f"REFUSING TO PUBLISH: lender keys in shared payload: {sorted(bad_keys)}")
    payload["lender"] = lender
    print(f"payload: {len(json.dumps(payload))} bytes, "
          f"{len(payload['steps'])} steps, {len(payload['documents'])} documents, "
          f"{len(payload['openItems'])} open items, {len(payload['people'])} people, "
          f"lender section {len(json.dumps(lender))} bytes (Matt slice only), "
          f"flags: {len(flags_shared)} shared, {len(flags_matt)} Matt-only "
          f"({', '.join(f['id'] for f in flags_shared + flags_matt)})")
    if dry:
        print("dry run: not sent")
        return
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    data = json.dumps({"payload": payload}).encode()
    req = urllib.request.Request(WORKER_URL + "/admin/publish", data=data, method="POST")
    req.add_header("User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("publish:", resp.status, resp.read().decode()[:300])
    except urllib.error.HTTPError as e:
        print("publish HTTP", e.code, e.read().decode("utf-8", "replace")[:500])
        raise

if __name__ == "__main__":
    main()
