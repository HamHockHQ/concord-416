#!/usr/bin/env python3
"""Deterministic cross-reference checks for the 416 dashboard (the flagger).

Compares structured fields across the closing record (tracker snapshot +
DASHBOARD_DATA.json), the lender dataset, and the CONFLICTS.md register,
and emits flags on mismatches. Every check is a real comparison of the
source data: when the underlying mismatch is fixed in the source files,
the flag stops being emitted on the next publish.

Visibility model: each flag is either shared (figure-free text, carries a
personTag, sliced by the Worker like every other record) or Matt-only
(contains money figures or lender-offer internals; published inside the
Matt-only `lender` section). Shared flag text is constructed without
figures here, and the publish guard re-verifies that no currency reaches
the shared payload.

Flag shape: {id, severity (info|warning|conflict), title, detail,
sources, detected (ISO date), personTag}
"""
import re
from datetime import date, datetime, timedelta

CURRENCY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")
TOL = 0.02


def parse_date(s):
    if not s:
        return None
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def business_days_between(a, b):
    """Weekdays strictly after a, up to and including b (a <= b)."""
    if b <= a:
        return 0
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def money(n):
    return f"${n:,.2f}"


def build_flags(snapshot, dd, lender, conflicts_text, today):
    matt, shared = [], []

    def add_matt(fid, severity, title, detail, sources):
        matt.append({"id": fid, "severity": severity, "title": title,
                     "detail": detail, "sources": sources,
                     "detected": today.isoformat(), "personTag": "Matt"})

    def add_shared(fid, severity, title, detail, sources, tag):
        # Shared flags must be figure-free; enforce here as well as in
        # the publish guard.
        assert not CURRENCY_RE.search(title + detail), fid
        shared.append({"id": fid, "severity": severity, "title": title,
                       "detail": detail, "sources": sources,
                       "detected": today.isoformat(), "personTag": tag})

    prop = dd.get("property", {}) or {}
    price = lender.get("price")
    terms = ((dd.get("lenders", {}) or {}).get("movement", {}) or {}).get(
        "at_signed_416_terms", {}) or {}
    scenarios = lender.get("scenarios", {}) or {}

    # ---- 1. Contract terms vs current lender quotes -------------------
    # The current scenario is the one Matt decided (3% down, Nov 20
    # reruns). Contract terms come from the signed BEC/ASR record in
    # DASHBOARD_DATA.json.
    current = scenarios.get("3")
    if current and terms:
        c_down = terms.get("down_pct")
        c_loan = terms.get("loan_amount")
        q_down_amt = current.get("downPayment")
        q_loan = current.get("loan")
        q_down_pct = round(100.0 * q_down_amt / price) if price and q_down_amt else None
        if c_loan and q_loan and (c_loan != q_loan or c_down != q_down_pct):
            add_matt(
                "contract-vs-quotes", "conflict",
                "Contract financing terms do not match the current lender quotes",
                f"The signed contract record shows {c_down}% down and a {money(c_loan)} loan, "
                f"while both lenders' current quotes are built on {q_down_pct}% down and a "
                f"{money(q_loan)} loan. The contract amendment to the decided terms is pending.",
                "DASHBOARD_DATA.json (signed BEC/ASR terms) vs lender_data.json (Nov 20, 2026 quotes)")
            add_shared(
                "contract-amendment-pending", "warning",
                "Contract amendment pending",
                "The signed contract's financing terms do not match the current lender quotes. "
                "The amendment to the decided terms is still pending.",
                "Closing record vs current lender quotes", "Carole")
        # Seller credit consistency: every current offer's seller-credit
        # line must equal the contract's 3% of price.
        expected_credit = round(0.03 * price, 2) if price else None
        for offer in current.get("offers", []):
            for c in (offer.get("components", {}) or {}).get("credits", []):
                if c.get("label") == "Seller credit" and expected_credit is not None \
                        and abs(c.get("amount", 0) - expected_credit) > TOL:
                    add_matt(
                        f"seller-credit-{offer['id']}", "conflict",
                        f"Seller credit mismatch in {offer['name']}",
                        f"{offer['name']} carries a seller credit of {money(c['amount'])}, "
                        f"but the contract's 3% seller credit on {money(price)} is {money(expected_credit)}.",
                        "lender_data.json credits vs contract seller credit (3% of price)")

    # ---- 2. Arithmetic re-verification of every published offer -------
    for skey, scen in scenarios.items():
        taxes = scen.get("taxesMonthly", 0)
        ins = scen.get("insuranceMonthly", 0)
        loan = scen.get("loan")
        for offer in scen.get("offers", []):
            comp_sum = offer["pi"] + taxes + ins + offer["mi"]
            if abs(comp_sum - offer["monthly"]) > TOL:
                add_matt(
                    f"arith-monthly-{offer['id']}", "conflict",
                    f"Monthly total does not add up in {offer['name']}",
                    f"Components sum to {money(comp_sum)} (P&I {money(offer['pi'])} + taxes "
                    f"{money(taxes)} + insurance {money(ins)} + MI {money(offer['mi'])}) but the "
                    f"stated monthly total is {money(offer['monthly'])}.",
                    f"lender_data.json, {offer['lender']} worksheet dated {offer['date']}")
            if offer.get("lifetimeInterest") is not None:
                expect = round(offer["pi"] * 360 - loan, 2)
                if abs(expect - offer["lifetimeInterest"]) > 1.00:
                    add_matt(
                        f"arith-lifetime-{offer['id']}", "warning",
                        f"Lifetime interest mismatch in {offer['name']}",
                        f"P&I x 360 minus the loan gives {money(expect)}, but the recorded "
                        f"lifetime interest is {money(offer['lifetimeInterest'])}.",
                        "lender_data.json (recomputed from P&I and loan)")
            comp = offer.get("components") or {}
            credits = comp.get("credits") or []
            if credits and comp.get("statedTotalCredits") is not None:
                line_sum = round(sum(c["amount"] for c in credits), 2)
                stated = comp["statedTotalCredits"]
                if abs(line_sum - stated) > TOL:
                    diff = round(stated - line_sum, 2)
                    add_matt(
                        f"credits-once-{offer['id']}", "conflict",
                        f"Credits do not add up in {offer['name']}: a credit is counted more than once",
                        f"The credit lines on the {offer['lender']} worksheet sum to {money(line_sum)}, "
                        f"but its stated Total Credits is {money(stated)}, a difference of {money(diff)}. "
                        f"This is the double-count pattern: a credit line is counted twice in the total. "
                        f"Corrected cash to close is {money(offer['cashToClose'])}.",
                        f"lender_data.json components vs the worksheet's stated Total Credits ({offer['date']})")
            if credits and comp.get("totalCosts") is not None and comp.get("statedCash") is not None \
                    and comp.get("statedTotalCredits") is not None:
                expect_cash = round(comp["totalCosts"] - comp["statedTotalCredits"], 2)
                if abs(expect_cash - comp["statedCash"]) > TOL:
                    add_matt(
                        f"arith-cash-{offer['id']}", "conflict",
                        f"Cash to close does not reconcile in {offer['name']}",
                        f"Total costs {money(comp['totalCosts'])} minus total credits "
                        f"{money(comp['statedTotalCredits'])} gives {money(expect_cash)}, but the "
                        f"worksheet states cash to close of {money(comp['statedCash'])}.",
                        "lender_data.json components (costs, credits, stated cash)")

    # ---- 3. Staleness and expiry --------------------------------------
    for scen in scenarios.values():
        for offer in scen.get("offers", []):
            qd = parse_date(offer.get("date"))
            if qd and business_days_between(qd, today) > 3:
                add_matt(
                    f"stale-{offer['id']}", "warning",
                    f"Stale quote: {offer['name']}",
                    f"This quote is dated {offer['date']}, more than 3 business days before "
                    f"today. Rates move daily; treat the figures as out of date until refreshed.",
                    "lender_data.json quote date vs publish date")
    pre = ((dd.get("lenders", {}) or {}).get("movement", {}) or {}).get("preapproval", {}) or {}
    exp = parse_date(pre.get("expires"))
    if exp and exp <= today:
        add_matt(
            "preapproval-expired", "warning",
            "Movement pre-approval has expired",
            f"The Movement pre-approval on file expired {exp.strftime('%b %d, %Y')} "
            f"(issued {pre.get('issued', 'earlier')} on the earlier, higher price scenario). "
            "Extending it requires a new credit pull; Movement confirmed this on Nov 20, 2026.",
            "DASHBOARD_DATA.json (lenders.movement.preapproval) vs today's date")

    # ---- 4. Documents marked Needed past their step's due date -------
    steps = snapshot.get("steps", [])
    execution = parse_date(dd.get("execution_date"))
    doc_step_keywords = [
        ("appraisal", "appraisal"),
        ("lender document package", "application"),
        ("closing disclosure", "settlement"),
    ]

    def step_due_date(step):
        dn = (step.get("dueNote") or "")
        d = parse_date(dn)
        if d:
            return d
        m = re.search(r"within (?:about )?(\d+) days", dn, re.I)
        if m and execution:
            return execution + timedelta(days=int(m.group(1)))
        return None

    for doc in snapshot.get("documents", []):
        if doc.get("status") != "Needed":
            continue
        name_l = (doc.get("name") or "").lower()
        kw = next((k for frag, k in doc_step_keywords if frag in name_l), None)
        if not kw:
            continue
        step = next((s for s in steps if kw in (s.get("title") or "").lower()), None)
        if not step:
            continue
        due = step_due_date(step)
        if due and due < today:
            add_shared(
                f"doc-overdue-{doc.get('position')}", "warning",
                f"Document overdue: {doc.get('name')}",
                f"This document is still marked Needed and its step ({step.get('title')}) "
                f"was due {due.strftime('%b %d, %Y')}.",
                "Tracker documents vs step due dates", doc.get("personTag") or "All parties")

    # ---- 5. Version conflicts: same document slot, different content --
    by_name = {}
    for doc in snapshot.get("documents", []):
        by_name.setdefault(doc.get("name"), []).append(doc)
    for name, docs in by_name.items():
        variants = {(d.get("status"), d.get("detail")) for d in docs}
        if len(docs) > 1 and len(variants) > 1:
            add_shared(
                f"doc-version-{abs(hash(name)) % 99999}", "conflict",
                f"Conflicting versions of one document: {name}",
                "Two records claim this document slot with different statuses or details. "
                "One of them is out of date.",
                "Tracker documents (duplicate slot)", "All parties")

    # ---- 6. CONFLICTS.md register: surface the open items -------------
    # The register stays the source of truth; its text is parsed, never
    # duplicated into code. RESOLVED and SUPERSEDED items do not flag.
    if conflicts_text:
        sections = re.split(r"\n## (C\d+)\.\s+", "\n" + conflicts_text)
        # sections: [pre, id, body, id, body, ...]
        for i in range(1, len(sections) - 1, 2):
            cid, body = sections[i], sections[i + 1]
            title = body.split("\n", 1)[0].strip()
            sm = re.search(r"Status:\s*([A-Z]+)", body)
            status = sm.group(1) if sm else ""
            if status in ("RESOLVED", "SUPERSEDED", ""):
                continue
            severity = {"OPEN": "conflict", "DECIDED": "warning",
                        "NOTED": "info"}.get(status, "info")
            bullets = [ln.strip()[2:].strip() for ln in body.splitlines()
                       if ln.strip().startswith("- ")]
            detail = " ".join(bullets)
            detail = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", detail)
            detail = re.sub(r"\s+", " ", detail).strip()
            if len(detail) > 460:
                detail = detail[:457].rsplit(" ", 1)[0] + "..."
            has_money = bool(CURRENCY_RE.search(title + " " + detail))
            if has_money or cid != "C7":
                add_matt(f"register-{cid.lower()}", severity,
                         f"Conflicts register {cid}: {title}",
                         detail + f" (Register status: {status}.)",
                         f"CONFLICTS.md {cid}")
            else:
                add_shared(f"register-{cid.lower()}", severity,
                           f"Conflicts register {cid}: {title}",
                           detail + f" (Register status: {status}.)",
                           f"CONFLICTS.md {cid}", "All parties")

    return shared, matt
