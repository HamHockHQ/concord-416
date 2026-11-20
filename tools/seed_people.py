#!/usr/bin/env python3
"""Seed the auth_people roster on concord-416-api (idempotent, by name).

Roster per Matt's 2026-11-20 decision: everyone the tracker is designed
for, each bound to their personTag slice. Emails are the ones on file in
the CLOSING_PROCESS README; people without an email on file get null
(their address is part of still-open Q2; admin link generation by person
id works regardless). Sarah Tompos (Title) is seeded DISABLED: her access
stays off until the title selection is confirmed.

Usage: seed_people.py
The admin token is read from /tmp/concord-416/token (never printed).
"""
import json, urllib.request, urllib.error

TOKEN_FILE = "/tmp/concord-416/token"
WORKER_URL = "https://concord-416-api.howardmd1.workers.dev"

PEOPLE = [
    {"name": "Matt Howard", "email": "matt@curiouser.io",
     "role": "Buyer", "tag": "Matt", "enabled": True},
    {"name": "Carole Kirchner", "email": "carole@caroleschoicehomes.com",
     "role": "Buyer's agent", "tag": "Carole", "enabled": True},
    {"name": "Julie Peteritas", "email": None,
     "role": "Sellers' agent", "tag": "Seller side", "enabled": True},
    {"name": "Silas R. Crews", "email": None,
     "role": "Seller", "tag": "Seller side", "enabled": True},
    {"name": "Emily Stednick", "email": None,
     "role": "Seller", "tag": "Seller side", "enabled": True},
    {"name": "Jim Rossiter", "email": "james.rossiter@movement.com",
     "role": "Lender (Movement Mortgage)", "tag": "Lender", "enabled": True},
    {"name": "Sam Totoro", "email": "totoros@univest.net",
     "role": "Lender (Univest)", "tag": "Lender", "enabled": True},
    {"name": "Sarah Tompos", "email": None,
     "role": "Title (Homesale Settlement Services)", "tag": "Title",
     "enabled": False},
]

def main():
    with open(TOKEN_FILE) as f:
        token = f.read().strip()
    data = json.dumps({"people": PEOPLE}).encode()
    req = urllib.request.Request(WORKER_URL + "/admin/seed", data=data, method="POST")
    req.add_header("User-Agent", "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", "Bearer " + token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            print("seed:", resp.status, resp.read().decode()[:800])
    except urllib.error.HTTPError as e:
        print("seed HTTP", e.code, e.read().decode("utf-8", "replace")[:500])
        raise

if __name__ == "__main__":
    main()
