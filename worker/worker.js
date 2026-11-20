// concord-416-api: shared dashboard backend for the 416 N Concord closing.
// Pattern copied from ttt-sync (~/workspace/pushup-sync/worker/worker.js):
// one Durable Object instance ("main") with SQLite storage. (D1 is not used:
// the account API token has no D1 scope, verified 2026-11-18.)
//
// Auth design: RESEARCH.md candidate 1 (DIY opaque token).
// - Magic-link tokens: 32 random bytes, only the SHA-256 hash is stored,
//   15-minute expiry, atomic single-use consume, new link invalidates old.
// - Scanner-safe: GET /signin only renders a confirm page and spends
//   nothing; redemption happens on an explicit POST from the confirm tap.
// - Sessions: opaque credential, SHA-256 hash at rest, HttpOnly cookie on
//   this origin plus a Bearer credential the Pages front end stores and
//   presents per call (RESEARCH.md Step 0.2 fallback, named tradeoff).
// - Money data is never published to this Worker at all (R3 by
//   construction): the publish endpoint receives an allowlist payload.
// Admin endpoints require Authorization: Bearer <PUBLISH_TOKEN> (secret).

const PAGES_ORIGIN = "https://hamhockhq.github.io";
const PAGES_URL = "https://hamhockhq.github.io/concord-416/";
const TOKEN_TTL_MS = 15 * 60 * 1000;
const SESSION_TTL_MATT_MS = 7 * 24 * 60 * 60 * 1000;
const SESSION_TTL_OTHER_MS = 24 * 60 * 60 * 1000;
const GENERIC_REQUEST_MSG =
  "If that address has access, a sign-in link is on its way.";

function corsHeaders(req) {
  const origin = req.headers.get("Origin") || "";
  const h = { "Vary": "Origin" };
  if (origin === PAGES_ORIGIN) {
    h["Access-Control-Allow-Origin"] = PAGES_ORIGIN;
    h["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS";
    h["Access-Control-Allow-Headers"] = "Authorization, Content-Type";
    h["Access-Control-Allow-Credentials"] = "true";
    h["Access-Control-Max-Age"] = "86400";
  }
  return h;
}

function json(req, obj, status = 200, extra = {}) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      "Content-Type": "application/json",
      "Cache-Control": "no-store",
      ...corsHeaders(req),
      ...extra,
    },
  });
}

function bearerOk(req, secret) {
  const h = req.headers.get("Authorization") || "";
  const want = "Bearer " + (secret || "");
  if (!secret || h.length !== want.length) return false;
  let diff = 0;
  for (let i = 0; i < h.length; i++) diff |= h.charCodeAt(i) ^ want.charCodeAt(i);
  return diff === 0;
}

function randomToken() {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function sha256hex(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sessionFromRequest(req) {
  const h = req.headers.get("Authorization") || "";
  if (h.startsWith("Bearer ") && h.length > 7) return h.slice(7);
  const cookie = req.headers.get("Cookie") || "";
  const m = cookie.match(/(?:^|;\s*)c416session=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export class DashboardStore {
  constructor(state, env) {
    this.state = state;
    this.env = env;
    this.sql = state.storage.sql;
    this.sql.exec(`CREATE TABLE IF NOT EXISTS kv (
      key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS auth_people (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL, email TEXT UNIQUE, role TEXT NOT NULL, tag TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1, chosen INTEGER NOT NULL DEFAULT 0,
      created_at INTEGER NOT NULL, disabled_at INTEGER)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS auth_login_tokens (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      person_id INTEGER NOT NULL, token_hash TEXT NOT NULL UNIQUE,
      created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, used_at INTEGER)`);
    this.sql.exec(`CREATE INDEX IF NOT EXISTS idx_tokens_person ON auth_login_tokens (person_id, created_at)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS auth_sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      person_id INTEGER NOT NULL, session_hash TEXT NOT NULL UNIQUE,
      created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL,
      revoked_at INTEGER, last_seen_at INTEGER)`);
    this.sql.exec(`CREATE TABLE IF NOT EXISTS link_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      person_id INTEGER NOT NULL, email TEXT NOT NULL,
      created_at INTEGER NOT NULL, handled_at INTEGER)`);
  }

  rows(sql, ...params) {
    return [...this.sql.exec(sql, ...params)];
  }

  getKv(key) {
    const r = this.rows(`SELECT value FROM kv WHERE key = ?`, key);
    return r.length ? JSON.parse(r[0].value) : null;
  }

  setKv(key, value) {
    this.sql.exec(
      `INSERT INTO kv (key, value, updated_at) VALUES (?, ?, ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at`,
      key, JSON.stringify(value), Date.now()
    );
  }

  getConfig() {
    return this.getKv("config") || {
      disableLendersAfter: null,
      disableAllAfter: null,
    };
  }

  personById(id) {
    const r = this.rows(`SELECT * FROM auth_people WHERE id = ?`, id);
    return r.length ? r[0] : null;
  }

  async validateSession(raw) {
    if (!raw) return null;
    const hash = await sha256hex(raw);
    const r = this.rows(`SELECT * FROM auth_sessions WHERE session_hash = ?`, hash);
    if (!r.length) return null;
    const s = r[0];
    const now = Date.now();
    if (s.revoked_at || s.expires_at <= now) return null;
    const person = this.personById(s.person_id);
    if (!person || !person.enabled) return null;
    const cfg = this.getConfig();
    if (person.tag !== "Matt") {
      if (cfg.disableAllAfter && now > cfg.disableAllAfter) return null;
      if (
        cfg.disableLendersAfter &&
        now > cfg.disableLendersAfter &&
        person.tag === "Lender" &&
        !person.chosen
      )
        return null;
    }
    this.sql.exec(`UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?`, now, s.id);
    return person;
  }

  sliceFor(person) {
    const payload = this.getKv("payload");
    if (!payload) return null;
    if (person.tag === "Matt") return payload;
    const vis = (item) =>
      item.personTag === person.tag || item.personTag === "All parties";
    return {
      meta: payload.meta,
      deadlines: payload.deadlines,
      people: payload.people,
      steps: payload.steps.filter(vis),
      documents: payload.documents.filter(vis),
      openItems: payload.openItems.filter(vis),
      viewer: { name: person.name, role: person.role, tag: person.tag },
    };
  }

  async op(req) {
    const url = new URL(req.url);
    const op = url.pathname;
    try {
      if (op === "/peek") {
        const { token } = await req.json();
        if (typeof token !== "string" || !token) return { valid: false };
        const hash = await sha256hex(token);
        const r = this.rows(
          `SELECT t.*, p.name AS pname, p.enabled AS penabled FROM auth_login_tokens t
           JOIN auth_people p ON p.id = t.person_id WHERE t.token_hash = ?`,
          hash
        );
        if (!r.length) return { valid: false };
        const t = r[0];
        const valid = !t.used_at && t.expires_at > Date.now() && !!t.penabled;
        return {
          valid,
          firstName: valid ? t.pname.split(" ")[0] : undefined,
        };
      }

      if (op === "/redeem") {
        const { token } = await req.json();
        if (typeof token !== "string" || !token) return { ok: false };
        const hash = await sha256hex(token);
        const now = Date.now();
        // Atomic single-use consume: one UPDATE guarded on unused + unexpired.
        this.sql.exec(
          `UPDATE auth_login_tokens SET used_at = ?
           WHERE token_hash = ? AND used_at IS NULL AND expires_at > ?`,
          now, hash, now
        );
        const changed = this.rows(`SELECT changes() AS c`)[0].c;
        if (!changed) return { ok: false };
        const t = this.rows(
          `SELECT * FROM auth_login_tokens WHERE token_hash = ?`, hash
        )[0];
        const person = this.personById(t.person_id);
        if (!person || !person.enabled) return { ok: false };
        const rawSession = randomToken();
        const sessionHash = await sha256hex(rawSession);
        const ttl =
          person.tag === "Matt" ? SESSION_TTL_MATT_MS : SESSION_TTL_OTHER_MS;
        this.sql.exec(
          `INSERT INTO auth_sessions (person_id, session_hash, created_at, expires_at, last_seen_at)
           VALUES (?, ?, ?, ?, ?)`,
          person.id, sessionHash, now, now + ttl, now
        );
        return {
          ok: true,
          session: rawSession,
          ttlSeconds: Math.floor(ttl / 1000),
          person: { name: person.name, role: person.role, tag: person.tag },
        };
      }

      if (op === "/request") {
        const { email } = await req.json();
        const norm = typeof email === "string" ? email.trim().toLowerCase() : "";
        const generic = { ok: true, message: GENERIC_REQUEST_MSG };
        if (!norm || !norm.includes("@")) return generic;
        const r = this.rows(
          `SELECT * FROM auth_people WHERE email = ? AND enabled = 1`, norm
        );
        if (!r.length) return generic;
        const person = r[0];
        const now = Date.now();
        const recent = this.rows(
          `SELECT COUNT(*) AS n FROM link_requests WHERE person_id = ? AND created_at > ?`,
          person.id, now - 60 * 1000
        )[0].n;
        const hourly = this.rows(
          `SELECT COUNT(*) AS n FROM link_requests WHERE person_id = ? AND created_at > ?`,
          person.id, now - 60 * 60 * 1000
        )[0].n;
        if (recent >= 1 || hourly >= 5) return generic; // rate-limited: no row, same response
        this.sql.exec(
          `INSERT INTO link_requests (person_id, email, created_at) VALUES (?, ?, ?)`,
          person.id, norm, now
        );
        return generic;
      }

      if (op === "/session") {
        const { s } = await req.json();
        const person = await this.validateSession(s);
        if (!person) return { ok: false };
        return {
          ok: true,
          person: { name: person.name, role: person.role, tag: person.tag },
        };
      }

      if (op === "/dashboard") {
        const { s } = await req.json();
        const person = await this.validateSession(s);
        if (!person) return { ok: false, error: "unauthorized" };
        const slice = this.sliceFor(person);
        if (!slice) return { ok: false, error: "no data published yet" };
        return { ok: true, dashboard: slice };
      }

      if (op === "/signout") {
        const { s } = await req.json();
        if (s) {
          const hash = await sha256hex(s);
          this.sql.exec(
            `UPDATE auth_sessions SET revoked_at = ? WHERE session_hash = ? AND revoked_at IS NULL`,
            Date.now(), hash
          );
        }
        return { ok: true };
      }

      // ---- admin ops (PUBLISH_TOKEN checked by the router) ----
      if (op === "/admin/publish") {
        const { payload } = await req.json();
        if (!payload || typeof payload !== "object") return { ok: false };
        this.setKv("payload", payload);
        this.setKv("payload_meta", {
          publishedAt: Date.now(),
          bytes: JSON.stringify(payload).length,
        });
        return { ok: true, bytes: JSON.stringify(payload).length };
      }

      if (op === "/admin/seed") {
        const { people } = await req.json();
        const out = [];
        for (const p of people) {
          const email = p.email ? String(p.email).toLowerCase() : null;
          const existing = this.rows(
            `SELECT id FROM auth_people WHERE name = ?`, p.name
          );
          if (existing.length) {
            this.sql.exec(
              `UPDATE auth_people SET email = ?, role = ?, tag = ?, enabled = ? WHERE id = ?`,
              email, p.role, p.tag, p.enabled ? 1 : 0, existing[0].id
            );
            out.push({ name: p.name, id: existing[0].id, action: "updated" });
          } else {
            const now = Date.now();
            this.sql.exec(
              `INSERT INTO auth_people (name, email, role, tag, enabled, created_at, disabled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)`,
              p.name, email, p.role, p.tag, p.enabled ? 1 : 0, now,
              p.enabled ? null : now
            );
            const id = this.rows(`SELECT last_insert_rowid() AS id`)[0].id;
            out.push({ name: p.name, id, action: "inserted" });
          }
        }
        return { ok: true, people: out };
      }

      if (op === "/admin/people") {
        const people = this.rows(`SELECT * FROM auth_people ORDER BY id`).map((p) => ({
          ...p,
          outstandingTokens: this.rows(
            `SELECT COUNT(*) AS n FROM auth_login_tokens WHERE person_id = ? AND used_at IS NULL AND expires_at > ?`,
            p.id, Date.now()
          )[0].n,
          activeSessions: this.rows(
            `SELECT COUNT(*) AS n FROM auth_sessions WHERE person_id = ? AND revoked_at IS NULL AND expires_at > ?`,
            p.id, Date.now()
          )[0].n,
        }));
        return { ok: true, people };
      }

      if (op === "/admin/person") {
        const { id, enabled, chosen } = await req.json();
        const person = this.personById(id);
        if (!person) return { ok: false, error: "no such person" };
        const now = Date.now();
        if (typeof enabled === "boolean") {
          this.sql.exec(
            `UPDATE auth_people SET enabled = ?, disabled_at = ? WHERE id = ?`,
            enabled ? 1 : 0, enabled ? null : now, id
          );
          if (!enabled) {
            // R4 kill switch: one call kills outstanding links and live sessions.
            this.sql.exec(`DELETE FROM auth_login_tokens WHERE person_id = ? AND used_at IS NULL`, id);
            this.sql.exec(
              `UPDATE auth_sessions SET revoked_at = ? WHERE person_id = ? AND revoked_at IS NULL`,
              now, id
            );
          }
        }
        if (typeof chosen === "boolean") {
          this.sql.exec(`UPDATE auth_people SET chosen = ? WHERE id = ?`, chosen ? 1 : 0, id);
        }
        return { ok: true, person: this.personById(id) };
      }

      if (op === "/admin/link") {
        const { personId } = await req.json();
        const person = this.personById(personId);
        if (!person) return { ok: false, error: "no such person" };
        if (!person.enabled) return { ok: false, error: "person is disabled" };
        const now = Date.now();
        const hourly = this.rows(
          `SELECT COUNT(*) AS n FROM auth_login_tokens WHERE person_id = ? AND created_at > ?`,
          person.id, now - 60 * 60 * 1000
        )[0].n;
        if (hourly >= 5) return { ok: false, error: "rate limited: 5 links per hour" };
        // New link invalidates the person's outstanding unused links.
        this.sql.exec(
          `DELETE FROM auth_login_tokens WHERE person_id = ? AND used_at IS NULL`,
          person.id
        );
        const raw = randomToken();
        const hash = await sha256hex(raw);
        const expiresAt = now + TOKEN_TTL_MS;
        this.sql.exec(
          `INSERT INTO auth_login_tokens (person_id, token_hash, created_at, expires_at)
           VALUES (?, ?, ?, ?)`,
          person.id, hash, now, expiresAt
        );
        const base = this.env.PUBLIC_BASE || "";
        return {
          ok: true,
          link: `${base}/signin?token=${raw}`,
          expiresAt,
          person: { id: person.id, name: person.name },
        };
      }

      if (op === "/admin/expire") {
        // Test hook (admin only): backdate a person's latest unused token so
        // the expiry path can be exercised in verification.
        const { personId } = await req.json();
        const r = this.rows(
          `SELECT id FROM auth_login_tokens WHERE person_id = ? AND used_at IS NULL ORDER BY created_at DESC LIMIT 1`,
          personId
        );
        if (!r.length) return { ok: false, error: "no unused token" };
        this.sql.exec(
          `UPDATE auth_login_tokens SET expires_at = ? WHERE id = ?`,
          Date.now() - 1000, r[0].id
        );
        return { ok: true };
      }

      if (op === "/admin/requests") {
        const requests = this.rows(
          `SELECT r.*, p.name AS person_name FROM link_requests r
           JOIN auth_people p ON p.id = r.person_id ORDER BY r.created_at DESC LIMIT 100`
        );
        return { ok: true, requests };
      }

      if (op === "/admin/config") {
        const body = await req.json().catch(() => ({}));
        if (body && Object.keys(body).length) {
          const cfg = { ...this.getConfig(), ...body };
          this.setKv("config", cfg);
          return { ok: true, config: cfg };
        }
        return { ok: true, config: this.getConfig() };
      }

      if (op === "/admin/state") {
        return {
          ok: true,
          payloadMeta: this.getKv("payload_meta"),
          payload: this.getKv("payload"),
          config: this.getConfig(),
          tokenHashes: this.rows(`SELECT token_hash, person_id, created_at, expires_at, used_at FROM auth_login_tokens ORDER BY id`),
          sessionHashes: this.rows(`SELECT session_hash, person_id, created_at, expires_at, revoked_at FROM auth_sessions ORDER BY id`),
          counts: {
            people: this.rows(`SELECT COUNT(*) AS n FROM auth_people`)[0].n,
            tokens: this.rows(`SELECT COUNT(*) AS n FROM auth_login_tokens`)[0].n,
            sessions: this.rows(`SELECT COUNT(*) AS n FROM auth_sessions`)[0].n,
            linkRequests: this.rows(`SELECT COUNT(*) AS n FROM link_requests`)[0].n,
          },
        };
      }

      return { ok: false, error: "unknown op" };
    } catch (err) {
      return { ok: false, error: String((err && err.message) || err) };
    }
  }

  async fetch(req) {
    const result = await this.op(req);
    const status = result && result.ok === false && result.error === "unauthorized" ? 401 : 200;
    return new Response(JSON.stringify(result), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  }
}

function confirmPage() {
  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>Sign in - Closing Tracker</title>
<style>
  body { margin:0; font-family: Inter, -apple-system, "Segoe UI", Roboto, sans-serif; background:#F4F4F3; color:#1c1c1e; }
  main { max-width: 420px; margin: 12vh auto; padding: 0 16px; }
  .card { background:#fff; border-radius: 18px; box-shadow: 0 8px 30px rgba(0,0,0,.08); padding: 28px; }
  h1 { font-size: 22px; margin: 0 0 8px; }
  p { color:#55575c; line-height: 1.5; }
  button { width:100%; padding: 14px; font-size: 16px; font-weight: 600; border:0; border-radius: 12px; background:#D63A2A; color:#fff; cursor:pointer; min-height:48px; }
  button:disabled { opacity:.55; cursor:default; }
  .msg { margin-top: 14px; font-size: 14px; }
</style>
</head>
<body>
<main>
  <div class="card">
    <h1 id="title">Checking your link...</h1>
    <p id="sub">One moment while we confirm this sign-in link.</p>
    <button id="go" disabled>Sign in</button>
    <p class="msg" id="msg" role="status"></p>
  </div>
</main>
<script>
(function () {
  var token = new URLSearchParams(location.search).get("token") || "";
  var title = document.getElementById("title");
  var sub = document.getElementById("sub");
  var go = document.getElementById("go");
  var msg = document.getElementById("msg");
  function dead(text) {
    title.textContent = "This link has expired or was already used";
    sub.textContent = text;
    go.style.display = "none";
  }
  if (!token) { dead("Request a fresh sign-in link and try again."); return; }
  fetch("/auth/peek", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: token }) })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (!d.valid) { dead("Request a fresh sign-in link and try again."); return; }
      title.textContent = "Sign in as " + d.firstName;
      sub.textContent = "Tap the button to open your dashboard. This link works once and expires soon.";
      go.disabled = false;
      go.addEventListener("click", function () {
        go.disabled = true;
        msg.textContent = "Signing you in...";
        fetch("/auth/redeem", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ token: token }) })
          .then(function (r) { return r.json(); })
          .then(function (res) {
            if (!res.ok) { dead("Request a fresh sign-in link and try again."); return; }
            location.replace("${PAGES_URL}#session=" + encodeURIComponent(res.session));
          })
          .catch(function () { msg.textContent = "Something went wrong. Try the button again."; go.disabled = false; });
      });
    })
    .catch(function () { dead("We could not check this link. Request a fresh one and try again."); });
})();
</script>
</body>
</html>`;
  return new Response(html, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Referrer-Policy": "no-referrer",
      "Cache-Control": "no-store",
    },
  });
}

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    if (req.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(req) });
    }
    const stub = env.STORE.get(env.STORE.idFromName("main"));
    const toDo = async (path, payload, extraHeaders = {}) => {
      const r = await stub.fetch("https://do" + path, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload || {}),
      });
      const text = await r.text();
      return new Response(text, {
        status: r.status,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          ...corsHeaders(req),
          ...extraHeaders,
        },
      });
    };

    try {
      if (url.pathname === "/" && req.method === "GET") {
        return json(req, { service: "concord-416-api", ok: true });
      }
      if (url.pathname === "/signin" && req.method === "GET") {
        return confirmPage(); // GET spends nothing (R8 scanner safety)
      }
      if (url.pathname === "/auth/peek" && req.method === "POST") {
        return toDo("/peek", await req.json());
      }
      if (url.pathname === "/auth/redeem" && req.method === "POST") {
        const body = await req.json();
        const r = await stub.fetch("https://do/redeem", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const data = JSON.parse(await r.text());
        const headers = {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
          ...corsHeaders(req),
        };
        if (data.ok && data.session) {
          headers["Set-Cookie"] =
            `c416session=${encodeURIComponent(data.session)}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${data.ttlSeconds || 86400}`;
        }
        return new Response(JSON.stringify(data), { status: r.status, headers });
      }
      if (url.pathname === "/auth/request" && req.method === "POST") {
        return toDo("/request", await req.json());
      }
      if (url.pathname === "/api/session" && req.method === "GET") {
        return toDo("/session", { s: sessionFromRequest(req) });
      }
      if (url.pathname === "/api/dashboard" && req.method === "GET") {
        return toDo("/dashboard", { s: sessionFromRequest(req) });
      }
      if (url.pathname === "/api/signout" && req.method === "POST") {
        return toDo("/signout", { s: sessionFromRequest(req) });
      }
      if (url.pathname.startsWith("/admin/")) {
        if (!bearerOk(req, env.PUBLISH_TOKEN)) {
          return json(req, { ok: false, error: "unauthorized" }, 401);
        }
        const op = url.pathname; // /admin/publish, /admin/seed, ...
        const body = req.method === "POST" ? await req.json().catch(() => ({})) : {};
        return toDo(op, body);
      }
      return json(req, { ok: false, error: "not found" }, 404);
    } catch (err) {
      return json(req, { ok: false, error: String((err && err.message) || err) }, 500);
    }
  },
};
