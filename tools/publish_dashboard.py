#!/usr/bin/env python3
"""Publish the 416 dashboard payload to the concord-416-api Worker.

Builds an ALLOWLIST payload from:
  - ~/workspace/concord-416/data/tracker_snapshot.json (steps, documents,
    open items, people, deadlines captured from the Muse tracker; the
    drift-check job refreshes this snapshot from the closing record)
  - ~/workspace/house-416-concord/closing-process/DASHBOARD_DATA.json
    (only the allowlisted meta fields below are read)

The payload NEVER contains money data: the `funds` section, lender figures,
and Matt's to-dos are excluded by construction (allowlist, not blocklist),
and a guard scan refuses to publish if a forbidden marker appears in the
serialized payload.

Usage: publish_dashboard.py [--dry-run]
The admin token is read from /tmp/concord-416/token (never printed).
"""
import json, os, sys, urllib.request, urllib.error

SNAPSHOT = os.path.expanduser("~/workspace/concord-416/data/tracker_snapshot.json")
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

def guard(payload):
    blob = json.dumps(payload)
    hits = [m for m in FORBIDDEN_MARKERS if m in blob]
    if hits:
        raise SystemExit(f"REFUSING TO PUBLISH: forbidden markers in payload: {hits}")

def main():
    dry = "--dry-run" in sys.argv
    payload = build_payload()
    guard(payload)
    print(f"payload: {len(json.dumps(payload))} bytes, "
          f"{len(payload['steps'])} steps, {len(payload['documents'])} documents, "
          f"{len(payload['openItems'])} open items, {len(payload['people'])} people")
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
