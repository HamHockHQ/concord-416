#!/usr/bin/env python3
"""Functional verification prep for concord-416-api (builder-run checks).

Exercises the RESEARCH.md Section 5 checklist items that test identities
allow, against the LIVE deployment, and writes raw results as JSON.
This is builder evidence, not the independent verdict: the build stays
UNVERIFIED until an independent check runs.

Usage: verify_functional.py <output.json>
Admin token from /tmp/concord-416/token (never printed, never in output).
"""
import json, subprocess, sys, time

WORKER = "https://concord-416-api.howardmd1.workers.dev"
UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
with open("/tmp/concord-416/token") as f:
    ADMIN = f.read().strip()

results = []
raw_secrets = []  # raw tokens/sessions used, for the hash audit only

def _curl(method, path, payload=None, bearer=None):
    args = ["curl", "-sS", "-X", method, "-A", UA, "-w", "\n%{http_code}"]
    if payload is not None:
        args += ["-H", "Content-Type: application/json", "-d", json.dumps(payload)]
    if bearer:
        args += ["-H", "Authorization: Bearer " + bearer]
    args.append(WORKER + path)
    out = subprocess.run(args, capture_output=True, text=True, timeout=90).stdout
    body, _, code = out.rpartition("\n")
    try:
        return int(code), json.loads(body)
    except ValueError:
        return int(code or 0), {"raw": body[:400]}

def call(method, path, payload=None, bearer=None, raw_headers=None):
    return _curl(method, path, payload, bearer)

def get_html(path):
    out = subprocess.run(
        ["curl", "-sS", "-A", UA, "-w", "\n%{http_code}", WORKER + path],
        capture_output=True, text=True, timeout=90).stdout
    body, _, code = out.rpartition("\n")
    return int(code or 0), body

def check(name, passed, detail):
    results.append({"test": name, "pass": bool(passed), "detail": detail})
    print(("PASS " if passed else "FAIL ") + name + " :: " + str(detail)[:220])

def admin(path, payload=None):
    return call("POST", path, payload if payload is not None else {}, bearer=ADMIN)

def people_by_name():
    s, b = admin("/admin/people")
    return {p["name"]: p for p in b.get("people", [])}

def make_link(person_id):
    s, b = admin("/admin/link", {"personId": person_id})
    assert b.get("ok"), f"link gen failed: {b}"
    link = b["link"]
    token = link.split("token=")[1]
    raw_secrets.append(token)
    return link, token

def redeem(token):
    s, b = call("POST", "/auth/redeem", {"token": token})
    if b.get("session"):
        raw_secrets.append(b["session"])
    return s, b

def dashboard(session):
    return call("GET", "/api/dashboard", bearer=session)

people = people_by_name()
MATT = people["Matt Howard"]["id"]
CAROLE = people["Carole Kirchner"]["id"]
JIM = people["Jim Rossiter"]["id"]
SAM = people["Sam Totoro"]["id"]
SARAH = people["Sarah Tompos"]["id"]

# --- baseline state
s, state0 = admin("/admin/state")
counts0 = state0["counts"]

# 1. Roster test: unknown email gets the generic response, creates nothing.
s, b = call("POST", "/auth/request", {"email": "nobody@example.com"})
s2, state1 = admin("/admin/state")
check("roster_unknown_email_generic_no_row",
      b.get("message", "").startswith("If that address has access")
      and state1["counts"]["linkRequests"] == counts0["linkRequests"]
      and state1["counts"]["tokens"] == counts0["tokens"],
      {"response": b, "linkRequests_before": counts0["linkRequests"],
       "linkRequests_after": state1["counts"]["linkRequests"]})

# 2. Rate limit: 6 requests for Carole inside a minute -> at most 1 new row.
before = state1["counts"]["linkRequests"]
for _ in range(6):
    call("POST", "/auth/request", {"email": "carole@caroleschoicehomes.com"})
s, state2 = admin("/admin/state")
created = state2["counts"]["linkRequests"] - before
check("rate_limit_six_requests_at_most_one_row", created <= 1,
      {"rows_created_by_6_requests": created})

# 3. Scanner test: GET of the sign-in link spends nothing; peek is valid.
link_c, token_c = make_link(CAROLE)
s, html = get_html("/signin?token=" + token_c)
s2, peek = call("POST", "/auth/peek", {"token": token_c})
s3, state3 = admin("/admin/state")
tok_row = [t for t in state3["tokenHashes"] if t["person_id"] == CAROLE]
check("scanner_get_spends_nothing",
      s == 200 and "Sign in" in html and peek.get("valid") is True
      and peek.get("firstName") == "Carole"
      and tok_row and tok_row[-1]["used_at"] is None,
      {"signin_status": s, "peek": peek,
       "token_used_at_after_get": tok_row[-1]["used_at"] if tok_row else "no row"})

# 4. Happy path + slice: Carole redeems. Documents and open items stay
# Carole + All parties only; her STEPS are the full timeline (Matt
# walkthrough, 2026-11-20: she keeps the buyer on track).
s, red = redeem(token_c)
carole_session = red.get("session")
s, dash = dashboard(carole_session)
d = dash.get("dashboard", {})
doc_names = [x["name"] for x in d.get("documents", [])]
step_titles = [x["title"] for x in d.get("steps", [])]
tags = {x["personTag"] for x in d.get("documents", []) + d.get("openItems", [])}
check("carole_slice_docs_items_restricted_steps_full_timeline",
      red.get("ok") and dash.get("ok")
      and "Buyer's Estimated Costs (BEC)" not in doc_names
      and "Wire Funds Policy" not in doc_names
      and "Appraisal Contingency Addendum" not in doc_names
      and "Fully executed agreement with seller signatures" in doc_names
      and tags <= {"Carole", "All parties"}
      and len(step_titles) == 12
      and "Mortgage application" in step_titles
      and "Settlement" in step_titles,
      {"doc_count": len(doc_names), "step_count": len(step_titles), "tags_seen": sorted(tags)})

# 5. Single use: second redeem of the same link fails, creates no session.
n_sessions = state3["counts"]["sessions"]
s, red2 = redeem(token_c)
s, state5 = admin("/admin/state")
check("single_use_second_redeem_fails",
      red2.get("ok") is False
      and state5["counts"]["sessions"] == n_sessions + 1,
      {"second_redeem": red2, "sessions_before": n_sessions,
       "sessions_after": state5["counts"]["sessions"]})

# 6. Matt sees the full view.
link_m, token_m = make_link(MATT)
s, redm = redeem(token_m)
matt_session = redm.get("session")
s, dashm = dashboard(matt_session)
dm = dashm.get("dashboard", {})
check("matt_full_view",
      dashm.get("ok") and len(dm.get("steps", [])) == 12
      and len(dm.get("documents", [])) == 14
      and len(dm.get("openItems", [])) == 4,
      {"steps": len(dm.get("steps", [])), "documents": len(dm.get("documents", [])),
       "openItems": len(dm.get("openItems", []))})

# 7. Money check: no funds data in the payload or in either slice.
# "19900" (Matt's cash-available default) legitimately appears inside
# Matt's own lender section since 2026-11-20; it must never appear in a
# non-Matt slice, so it is checked against Carole's slice only.
forbidden = ["5192.28", "liquid_for_sure", "balance_due", "cash_needed",
             "boa_checking", "ending_balance", '"funds":']
blobs = {"payload": json.dumps(state5.get("payload")),
         "carole_slice": json.dumps(d), "matt_slice": json.dumps(dm)}
hits = {k: [f for f in forbidden if f in v] for k, v in blobs.items()}
if "19900" in blobs["carole_slice"]:
    hits["carole_slice"].append("19900")
check("money_data_absent_everywhere", all(not v for v in hits.values()), hits)

# 8. New link invalidates old (Jim).
link_j1, token_j1 = make_link(JIM)
link_j2, token_j2 = make_link(JIM)
s, r_old = redeem(token_j1)
s, r_new = redeem(token_j2)
jim_session = r_new.get("session")
check("new_link_invalidates_old",
      r_old.get("ok") is False and r_new.get("ok") is True,
      {"old_redeem_ok": r_old.get("ok"), "new_redeem_ok": r_new.get("ok")})

# 9. Expiry: backdated token is refused (Sam).
link_s1, token_s1 = make_link(SAM)
admin("/admin/expire", {"personId": SAM})
s, peek_exp = call("POST", "/auth/peek", {"token": token_s1})
s, r_exp = redeem(token_s1)
check("expired_token_refused",
      peek_exp.get("valid") is False and r_exp.get("ok") is False,
      {"peek": peek_exp, "redeem_ok": r_exp.get("ok")})

# 10. Revocation: disabling Sam mid-session kills the session and links.
link_s2, token_s2 = make_link(SAM)
s, r_sam = redeem(token_s2)
sam_session = r_sam.get("session")
s, dash_ok = dashboard(sam_session)
admin("/admin/person", {"id": SAM, "enabled": False})
s, dash_dead = dashboard(sam_session)
s, link_dead = admin("/admin/link", {"personId": SAM})
admin("/admin/person", {"id": SAM, "enabled": True})  # restore
check("revocation_disable_kills_session_and_links",
      dash_ok.get("ok") is True and dash_dead.get("ok") is False
      and link_dead.get("ok") is False,
      {"before_disable_ok": dash_ok.get("ok"),
       "after_disable_ok": dash_dead.get("ok"),
       "link_while_disabled": link_dead})

# 11. Disabled-by-default person (Sarah, title) cannot get a link.
s, link_sarah = admin("/admin/link", {"personId": SARAH})
check("title_person_disabled_no_link",
      link_sarah.get("ok") is False,
      {"link_result": link_sarah})

# 12. Forged / absent session is refused.
s, dash_forged = dashboard("forged-session-value-1234567890")
s2, dash_none = call("GET", "/api/dashboard")
check("forged_or_absent_session_refused",
      dash_forged.get("ok") is False and dash_none.get("ok") is False,
      {"forged_status": s, "absent_status": s2})

# 13. Sign-out: Matt's session dies after signout.
s, so = call("POST", "/api/signout", bearer=matt_session)
s, sess_after = call("GET", "/api/session", bearer=matt_session)
check("signout_kills_session",
      so.get("ok") is True and sess_after.get("ok") is False,
      {"signout": so, "session_after": sess_after})

# 14. Hash audit: stored values are 64-hex hashes; no raw secret is stored.
s, stateF = admin("/admin/state")
hashes = [t["token_hash"] for t in stateF["tokenHashes"]] + \
         [x["session_hash"] for x in stateF["sessionHashes"]]
import re
all_hex = all(re.fullmatch(r"[0-9a-f]{64}", h) for h in hashes)
leaked = [h for h in hashes if h in raw_secrets]
check("hash_audit_hashes_only",
      all_hex and not leaked and len(hashes) > 0,
      {"stored_hash_count": len(hashes), "all_sha256_hex": all_hex,
       "raw_secrets_leaked": len(leaked)})

# 15. Auto-shutoff config exists and is unarmed.
cfg = stateF.get("config", {})
check("shutoff_config_present_unarmed",
      cfg.get("disableLendersAfter") is None and cfg.get("disableAllAfter") is None,
      {"config": cfg})

# 16. Walkthrough feedback (2026-11-20): lender-name scrub both ways.
# Sam's slice must never name Movement; Jim's must never name Univest.
JULIE = people["Julie Peteritas"]["id"]

def fresh_dash(pid):
    _, tok = make_link(pid)
    _, r = redeem(tok)
    _, dd2 = dashboard(r.get("session"))
    return dd2.get("dashboard", {})

sam_d = fresh_dash(SAM)
sam_blob = json.dumps(sam_d)
sam_people = {p["name"]: p for p in sam_d.get("people", [])}
sam_steps = [x["title"] for x in sam_d.get("steps", [])]
check("sam_slice_never_names_movement",
      "Movement" not in sam_blob
      and "Mortgage application" in sam_steps
      and not sam_people.get("Jim Rossiter", {}).get("org"),
      {"org_on_jim_row": sam_people.get("Jim Rossiter", {}).get("org"),
       "sam_step_count": len(sam_steps)})

jim_d = fresh_dash(JIM)
jim_blob = json.dumps(jim_d)
jim_people = {p["name"]: p for p in jim_d.get("people", [])}
check("jim_slice_never_names_univest",
      "Univest" not in jim_blob
      and not jim_people.get("Sam Totoro", {}).get("org"),
      {"org_on_sam_row": jim_people.get("Sam Totoro", {}).get("org")})

# 17. Negotiation summary lives in Matt's payload only, and no shared
# slice carries any currency amount.
import re as _re
matt_blob = json.dumps(dm)
others = {"carole": json.dumps(d), "sam": sam_blob, "jim": jim_blob}
# Markers must be distinctive to the lender negotiation section: the
# bare word "negotiation" also appears in the shared seller step title
# "Inspection results and repair negotiation", so it is not a leak.
leaks = {k: [m for m in ["beat these numbers", "444806", "lowest monthly"] if m in v]
         for k, v in others.items()}
cur_hits = {k: _re.findall(r"\$\s?\d[\d,]*(?:\.\d{2})?", v) for k, v in others.items()}
check("negotiation_matt_only_no_currency_in_shared_slices",
      "beat these numbers" in matt_blob
      and all(not v for v in leaks.values())
      and all(not v for v in cur_hits.values()),
      {"leaks": leaks, "currency": cur_hits})

# 18. Seller slice carries the inspection-results step; the stale open
# item is gone; Carole's step list is the full timeline.
julie_d = fresh_dash(JULIE)
julie_steps = [x["title"] for x in julie_d.get("steps", [])]
matt_items = [x["title"] for x in dm.get("openItems", [])]
check("seller_inspection_step_and_stale_item_removed",
      "Inspection results and repair negotiation" in julie_steps
      and "Seller execution confirmed" not in matt_items
      and len(step_titles) == len(dm.get("steps", [])),
      {"julie_steps": julie_steps, "matt_open_items": matt_items,
       "carole_step_count": len(step_titles)})

summary = {
    "ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    "worker": WORKER,
    "passed": sum(1 for r in results if r["pass"]),
    "total": len(results),
    "results": results,
}
with open(sys.argv[1], "w") as f:
    json.dump(summary, f, indent=1)
print(f"\n{summary['passed']}/{summary['total']} checks passed (builder-run, UNVERIFIED)")
