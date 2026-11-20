#!/usr/bin/env python3
"""Deploy the concord-416-api Worker through custom.cloudflare and smoke test it.

The publish/admin token is generated here, written to /tmp/concord-416/token
(0600, outside the workspace), set as the Worker secret PUBLISH_TOKEN, and
NEVER printed. Usage: deploy_api.py deploy | smoke
"""
import json, os, secrets, sys, urllib.request, urllib.error, uuid
sys.path.insert(0, "/opt/muse/skills/custom-connector-setup/bin")
from dynamic_credentials import add_surrogate_to_request, read_json_response

API = "https://api.cloudflare.com/client/v4"
HOSTS = ["api.cloudflare.com"]
ACCOUNT = "6d4ecc15644047a55ee339e5afc1b2ab"
SCRIPT = "concord-416-api"
TOKEN_FILE = "/tmp/concord-416/token"
WORKER_URL = "https://concord-416-api.howardmd1.workers.dev"

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise RuntimeError("redirect refused, verify the endpoint")

def cf(method, path, data=None, content_type=None):
    req = urllib.request.Request(API + path, data=data, method=method)
    if content_type:
        req.add_header("Content-Type", content_type)
    add_surrogate_to_request(req, "custom.cloudflare", allowed_hosts=HOSTS)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=90) as resp:
            return read_json_response(resp)
    except urllib.error.HTTPError as e:
        print("CF HTTP", e.code, e.read().decode("utf-8", "replace")[:1200])
        raise

def get_token():
    with open(TOKEN_FILE) as f:
        return f.read().strip()

def worker(method, path, token=None, payload=None, session=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(WORKER_URL + path, data=data, method=method)
    req.add_header("User-Agent", "Mozilla/5.0 (concord-416 build tooling)")
    if data:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    if session:
        req.add_header("Authorization", "Bearer " + session)
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=60) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, {"raw": raw[:200]}
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except ValueError:
            return e.code, {"raw": raw[:200]}

def script_exists():
    # The script-content GET returns a large multipart body that this
    # connector's JSON reader chokes on; the settings GET is small JSON.
    try:
        cf("GET", f"/accounts/{ACCOUNT}/workers/scripts/{SCRIPT}/settings")
        return True
    except Exception as e:
        print("script_exists check failed:", type(e).__name__)
        return False

def deploy():
    os.makedirs("/tmp/concord-416", exist_ok=True)
    if not os.path.exists(TOKEN_FILE):
        tok = secrets.token_urlsafe(32)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(tok)
        print("token: generated and stored (value not printed)")
    else:
        print("token: existing file reused (value not printed)")
    token = get_token()
    code = open(os.path.expanduser("~/workspace/concord-416/worker/worker.js"), "rb").read()
    boundary = "----muse" + uuid.uuid4().hex
    metadata = json.dumps({
        "main_module": "worker.js",
        "compatibility_date": "2026-11-01",
        "bindings": [
            {"name": "STORE", "type": "durable_object_namespace", "class_name": "DashboardStore"},
            {"name": "PUBLISH_TOKEN", "type": "secret_text", "text": token},
            {"name": "PUBLIC_BASE", "type": "plain_text", "text": WORKER_URL},
        ],
        # First deploy creates the class under tag v1. Redeploys must name
        # the existing tag as old_tag (Cloudflare error 10079 otherwise).
        "migrations": ({"old_tag": "v1", "new_tag": "v1", "steps": []}
                       if script_exists() else
                       {"new_tag": "v1", "steps": [{"new_sqlite_classes": ["DashboardStore"]}]}),
    }).encode()
    parts = []
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\nContent-Type: application/json\r\n\r\n".encode() + metadata + b"\r\n")
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"worker.js\"; filename=\"worker.js\"\r\nContent-Type: application/javascript+module\r\n\r\n".encode() + code + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    data = cf("PUT", f"/accounts/{ACCOUNT}/workers/scripts/{SCRIPT}", data=b"".join(parts),
              content_type=f"multipart/form-data; boundary={boundary}")
    r = data.get("result") or {}
    print("upload:", json.dumps({"success": data.get("success"), "id": r.get("id"), "modified_on": r.get("modified_on")}))
    sub = cf("POST", f"/accounts/{ACCOUNT}/workers/scripts/{SCRIPT}/subdomain",
               data=json.dumps({"enabled": True, "previews_enabled": False}).encode(), content_type="application/json")
    print("subdomain:", json.dumps({"success": sub.get("success"), "result": sub.get("result")}))

def smoke():
    token = get_token()
    out = {}
    s, b = worker("GET", "/")
    out["root"] = (s, b)
    s, b = worker("GET", "/api/dashboard")
    out["dashboard_no_session_status"] = s
    s, b = worker("POST", "/admin/people", token, {})
    out["admin_people_count"] = (s, len((b or {}).get("people", [])))
    s, b = worker("POST", "/admin/people", None, {})
    out["admin_no_token_status"] = s
    print(json.dumps(out, indent=1))

if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "deploy":
        deploy()
    elif len(sys.argv) == 2 and sys.argv[1] == "smoke":
        smoke()
    else:
        print(__doc__); sys.exit(2)
