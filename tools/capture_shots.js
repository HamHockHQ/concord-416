// Screenshots + axe-core pass for the external 416 dashboard (builder-run).
// Drives the REAL magic-link flow in a headless browser: admin generates a
// link (test only, nothing is sent), the browser opens the confirm page,
// taps Sign in, lands on the Pages dashboard, screenshots, signs out.
// Usage: node capture_shots.js <outdir>
// Admin token read from /tmp/concord-416/token; never printed or saved.
//
// Network note: this VM's egress requires an authenticated proxy and the
// headless Chromium build will not do proxy auth itself, so this script
// runs a tiny local no-auth relay that forwards CONNECT to the egress
// proxy with the credentials from the environment.
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");
const { chromium } = require("/home/hatch/workspace/family-cabin-a11y-test/node_modules/playwright-core");

function startRelay() {
  return new Promise((resolve) => {
    const u = new URL(process.env.HTTPS_PROXY || process.env.https_proxy);
    const auth = "Basic " + Buffer.from(
      decodeURIComponent(u.username) + ":" + decodeURIComponent(u.password)
    ).toString("base64");
    const server = http.createServer((req, res) => {
      const opts = {
        host: u.hostname, port: u.port, method: req.method,
        path: req.url, headers: { ...req.headers, "Proxy-Authorization": auth },
      };
      const up = http.request(opts, (ur) => { res.writeHead(ur.statusCode, ur.headers); ur.pipe(res); });
      up.on("error", () => { res.writeHead(502); res.end(); });
      req.pipe(up);
    });
    server.on("connect", (req, clientSocket, head) => {
      const upstream = net.connect(u.port, u.hostname, () => {
        upstream.write(
          `CONNECT ${req.url} HTTP/1.1\r\nHost: ${req.url}\r\n` +
          `Proxy-Authorization: ${auth}\r\nProxy-Connection: Keep-Alive\r\n\r\n`
        );
      });
      let buf = Buffer.alloc(0);
      const onData = (chunk) => {
        buf = Buffer.concat([buf, chunk]);
        const idx = buf.indexOf("\r\n\r\n");
        if (idx === -1) return;
        upstream.removeListener("data", onData);
        const statusLine = buf.slice(0, buf.indexOf("\r\n")).toString();
        if (!/ 200 /.test(statusLine + " ")) {
          clientSocket.end("HTTP/1.1 502 Bad Gateway\r\n\r\n");
          upstream.end();
          return;
        }
        clientSocket.write("HTTP/1.1 200 Connection Established\r\n\r\n");
        const rest = buf.slice(idx + 4);
        if (rest.length) clientSocket.write(rest);
        if (head && head.length) upstream.write(head);
        upstream.pipe(clientSocket);
        clientSocket.pipe(upstream);
      };
      upstream.on("data", onData);
      upstream.on("error", () => clientSocket.destroy());
      clientSocket.on("error", () => upstream.destroy());
    });
    server.listen(18080, "127.0.0.1", () => resolve(server));
  });
}

const WORKER = "https://concord-416-api.howardmd1.workers.dev";
const PAGES = "https://hamhockhq.github.io/concord-416/";
const EXE = "/home/hatch/.cache/ms-playwright/chromium_headless_shell-1255/chrome-headless-shell-linux64/chrome-headless-shell";
const AXE = "/home/hatch/workspace/family-cabin-a11y-test/node_modules/axe-core/axe.min.js";
const ADMIN = fs.readFileSync("/tmp/concord-416/token", "utf8").trim();
const OUT = process.argv[2];

async function adminPost(p, body) {
  const r = await fetch(WORKER + p, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: "Bearer " + ADMIN },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

async function signInFlow(page, personId) {
  const linkRes = await adminPost("/admin/link", { personId });
  if (!linkRes.ok) throw new Error("link gen failed: " + JSON.stringify(linkRes));
  await page.goto(linkRes.link, { waitUntil: "networkidle" });
  await page.waitForSelector("#go:not([disabled])", { timeout: 20000 });
  await page.click("#go");
  await page.waitForURL(/hamhockhq\.github\.io/, { timeout: 20000 });
  await page.waitForSelector("#app:not([hidden])", { timeout: 20000 });
  await page.waitForTimeout(600);
}

async function axeRun(page, label, results) {
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(async () => {
    return await axe.run(document, { resultTypes: ["violations"] });
  });
  results[label] = {
    url: page.url(),
    violations: res.violations.map((v) => ({
      id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length,
    })),
    violationCount: res.violations.length,
  };
  console.log("axe", label, "violations:", res.violations.length);
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const people = (await adminPost("/admin/people")).people;
  const byName = Object.fromEntries(people.map((p) => [p.name, p.id]));
  const relay = await startRelay();
  const browser = await chromium.launch({
    executablePath: EXE,
    args: ["--no-sandbox"],
    proxy: { server: "http://127.0.0.1:18080" },
  });
  const axeResults = {};

  const viewports = [
    { name: "desktop", width: 1280, height: 900 },
    { name: "mobile", width: 390, height: 844 },
  ];

  for (const vp of viewports) {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: vp.width, height: vp.height } });
    const page = await ctx.newPage();

    // Signed-out view
    await page.goto(PAGES, { waitUntil: "networkidle" });
    await page.waitForSelector("#signedout:not([hidden])", { timeout: 20000 });
    await page.screenshot({ path: path.join(OUT, `signed-out-${vp.name}.png`), fullPage: true });
    if (vp.name === "desktop") await axeRun(page, "signed-out", axeResults);
    console.log("shot signed-out", vp.name);

    // Matt view (full)
    await signInFlow(page, byName["Matt Howard"]);
    await page.screenshot({ path: path.join(OUT, `matt-view-${vp.name}.png`), fullPage: true });
    if (vp.name === "desktop") await axeRun(page, "matt-view", axeResults);
    console.log("shot matt-view", vp.name);
    await page.click("#signout");
    await page.waitForSelector("#signedout:not([hidden])", { timeout: 20000 });

    // Carole view (non-Matt slice)
    await signInFlow(page, byName["Carole Kirchner"]);
    await page.screenshot({ path: path.join(OUT, `carole-view-${vp.name}.png`), fullPage: true });
    if (vp.name === "desktop") await axeRun(page, "carole-view", axeResults);
    console.log("shot carole-view", vp.name);
    await page.click("#signout");
    await page.waitForSelector("#signedout:not([hidden])", { timeout: 20000 });

    await ctx.close();
  }

  // Confirm page shot (desktop): fresh link, no redeem.
  const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const linkRes = await adminPost("/admin/link", { personId: byName["Carole Kirchner"] });
  await page.goto(linkRes.link, { waitUntil: "networkidle" });
  await page.waitForSelector("#go:not([disabled])", { timeout: 20000 });
  await page.screenshot({ path: path.join(OUT, "confirm-page-desktop.png"), fullPage: true });
  await axeRun(page, "confirm-page", axeResults);
  console.log("shot confirm-page");
  await ctx.close();

  await browser.close();
  relay.close();
  fs.writeFileSync(path.join(OUT, "axe-report.json"), JSON.stringify(axeResults, null, 1));
  console.log("done");
})().catch((e) => { console.error("FAILED:", e.message); process.exit(1); });
