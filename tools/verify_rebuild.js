// Live verification of the interactive rebuild (2026-11-20).
// Drives the real magic-link flow against the LIVE Pages site and Worker:
// functional checks (tabs, timeline, calendar, expand, filters, lender
// scenarios, preview-as, flags) + axe-core on the changed views +
// money-leak assertions on non-Matt views. Test links only; nothing sent.
// Usage: node verify_rebuild.js <outdir>
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
    server.listen(18081, "127.0.0.1", () => resolve(server));
  });
}

const WORKER = "https://concord-416-api.howardmd1.workers.dev";
const PAGES = "https://hamhockhq.github.io/concord-416/";
const EXE = "/home/hatch/.cache/ms-playwright/chromium_headless_shell-1255/chrome-headless-shell-linux64/chrome-headless-shell";
const AXE = "/home/hatch/workspace/family-cabin-a11y-test/node_modules/axe-core/axe.min.js";
const ADMIN = fs.readFileSync("/tmp/concord-416/token", "utf8").trim();
const OUT = process.argv[2];
const CUR = /\$\s?\d[\d,]*(?:\.\d{2})?/;

const results = [];
function check(name, ok, evidence) {
  results.push({ name, ok: !!ok, evidence: String(evidence || "") });
  console.log((ok ? "PASS" : "FAIL"), "|", name, "|", String(evidence || "").slice(0, 140));
}

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
  await page.waitForTimeout(700);
}

async function axeRun(page, label, axeResults) {
  await page.addScriptTag({ path: AXE });
  const res = await page.evaluate(async () => await axe.run(document, { resultTypes: ["violations"] }));
  axeResults[label] = res.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length }));
  check("axe " + label, res.violations.length === 0, res.violations.length + " violations");
}

(async () => {
  fs.mkdirSync(OUT, { recursive: true });
  const people = (await adminPost("/admin/people")).people;
  const byName = Object.fromEntries(people.map((p) => [p.name, p.id]));
  const relay = await startRelay();
  const browser = await chromium.launch({
    executablePath: EXE,
    args: ["--no-sandbox"],
    proxy: { server: "http://127.0.0.1:18081" },
  });
  const axeResults = {};

  // ---------- signed-out ----------
  {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(PAGES, { waitUntil: "networkidle" });
    await page.waitForSelector("#signedout:not([hidden])", { timeout: 20000 });
    const hasNewCode = await page.evaluate(() => !!document.getElementById("tab-flags") && !!document.getElementById("tl-inner"));
    check("live page is the new build", hasNewCode, "tab-flags + tl-inner present in live DOM");
    await axeRun(page, "signed-out", axeResults);
    await ctx.close();
  }

  // ---------- Matt ----------
  {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 900 } });
    const page = await ctx.newPage();
    await signInFlow(page, byName["Matt Howard"]);

    const tabs = await page.evaluate(() => ({
      lendersHidden: document.getElementById("tab-lenders").hidden,
      flagsPill: document.getElementById("tab-flags-count").textContent,
      flagsPillHidden: document.getElementById("tab-flags-count").hidden,
      openPill: document.getElementById("tab-open-count").textContent,
      previewVisible: !document.getElementById("previewbar").hidden,
    }));
    check("Matt: lenders tab visible", tabs.lendersHidden === false, "tab-lenders hidden=" + tabs.lendersHidden);
    check("Matt: flags badge = 11", tabs.flagsPill === "11" && !tabs.flagsPillHidden, "pill=" + tabs.flagsPill);
    check("Matt: open items badge = 5", tabs.openPill === "5", "pill=" + tabs.openPill);
    check("Matt: preview-as control visible", tabs.previewVisible, "");
    await axeRun(page, "matt-overview", axeResults);

    // Deadlines: timeline
    await page.click("#tab-deadlines");
    await page.waitForTimeout(300);
    const tl = await page.evaluate(() => ({
      points: document.querySelectorAll(".tl-point").length,
      today: !!document.querySelector(".tl-today"),
      detail: document.getElementById("tl-detail").textContent,
    }));
    check("Matt: timeline renders 6 points + today marker", tl.points === 6 && tl.today, "points=" + tl.points + " today=" + tl.today);
    check("Matt: timeline auto-detail shows a deadline note", /execution|days|Dec|Nov|Jan/.test(tl.detail), tl.detail.slice(0, 100));
    await page.evaluate(() => {
      const pts = document.querySelectorAll(".tl-point");
      for (const p of pts) { if ((p.getAttribute("aria-label") || "").includes("Earnest")) { p.click(); return; } }
    });
    await page.waitForTimeout(200);
    const tlDetail = await page.evaluate(() => document.getElementById("tl-detail").textContent);
    check("Matt: tapping the deposit point shows its note", tlDetail.includes("5 days from execution"), tlDetail.slice(0, 120));
    await page.screenshot({ path: path.join(OUT, "matt-deadlines-timeline.png"), fullPage: true });
    await axeRun(page, "matt-deadlines", axeResults);

    // Calendar
    await page.click("#dl-view-cal");
    await page.waitForTimeout(300);
    const cal = await page.evaluate(() => ({
      months: document.querySelectorAll(".cal-month").length,
      marked: document.querySelectorAll("button.cal-day.has").length,
    }));
    check("Matt: calendar renders 3 months with 6 marked dates", cal.months === 3 && cal.marked === 6, JSON.stringify(cal));
    await page.evaluate(() => {
      const b = document.querySelector('button.cal-day.has[data-date="2026-11-23"]');
      if (b) b.click();
    });
    await page.waitForTimeout(200);
    const calDetail = await page.evaluate(() => document.getElementById("cal-detail").textContent);
    check("Matt: tapping Nov 23 shows the deposit deadline", /Earnest money deposit/.test(calDetail), calDetail.slice(0, 120));
    await page.screenshot({ path: path.join(OUT, "matt-calendar.png"), fullPage: true });

    // List expand
    await page.click("#dl-view-list");
    await page.waitForTimeout(200);
    await page.click("#deadlines .xbtn");
    await page.waitForTimeout(150);
    const exp = await page.evaluate(() => {
      const b = document.querySelector("#deadlines .xbtn");
      const panel = document.getElementById(b.getAttribute("aria-controls"));
      return { expanded: b.getAttribute("aria-expanded"), hidden: panel.hidden, text: panel.textContent };
    });
    check("Matt: deadline row expands in place", exp.expanded === "true" && exp.hidden === false && exp.text.length > 10, exp.text.slice(0, 100));

    // Steps filter
    await page.click("#tab-steps");
    await page.waitForTimeout(200);
    const before = await page.evaluate(() => document.querySelectorAll("#steps .xitem").length);
    await page.evaluate(() => {
      for (const b of document.querySelectorAll("#steps-filters .fchip")) if (b.textContent === "Done") b.click();
    });
    await page.waitForTimeout(200);
    const after = await page.evaluate(() => document.querySelectorAll("#steps .xitem").length);
    check("Matt: steps filter Done narrows 11 -> 2", before === 11 && after === 2, before + " -> " + after);

    // Lenders
    await page.click("#tab-lenders");
    await page.waitForTimeout(300);
    const lend1 = await page.evaluate(() => document.getElementById("lend-table").textContent);
    check("Matt: lender table shows the 3 current offers' cash figures",
      lend1.includes("$17,217.70") && lend1.includes("$16,237.65") && lend1.includes("$14,758.94"), "");
    check("Matt: lender table shows lifetime interest", lend1.includes("$463,249.40") && lend1.includes("$444,806.60"), "");
    await page.click("#lend-dp-5");
    await page.waitForTimeout(200);
    const lend5 = await page.evaluate(() => document.getElementById("lend-table").textContent);
    check("Matt: 5% toggle swaps in the Nov 19 figures", lend5.includes("$21,932.72") && lend5.includes("$20,557.01"), "");
    await page.click("#lend-dp-3");
    await page.click("#lend-hold-7");
    await page.waitForTimeout(200);
    const lend7 = await page.evaluate(() => document.getElementById("lend-table").textContent);
    check("Matt: 7-year horizon recomputes interest (computed label)", /Interest over 7 years/.test(lend7) && lend7.includes("(computed)"), "");
    await page.fill("#lend-cash", "15000");
    await page.waitForTimeout(250);
    const lendCash = await page.evaluate(() => document.getElementById("lend-table").textContent);
    check("Matt: cash-available 15000 shows Movement cash left -$2,217.70", lendCash.includes("-$2,217.70") || lendCash.includes("$-2,217.70"), lendCash.match(/Cash left[^$]*(\S+)/) ? "" : "");
    await page.screenshot({ path: path.join(OUT, "matt-lenders.png"), fullPage: true });
    await axeRun(page, "matt-lenders", axeResults);

    // Flags
    await page.click("#tab-flags");
    await page.waitForTimeout(200);
    const flagInfo = await page.evaluate(() => ({
      rows: document.querySelectorAll("#flags li").length,
      text: document.getElementById("flags").textContent,
    }));
    check("Matt: flags panel lists 11 flags", flagInfo.rows === 11, "rows=" + flagInfo.rows);
    check("Matt: flags include the Univest double-count catch", /Credits do not add up in Univest/.test(flagInfo.text), "");
    check("Matt: flags include the contract mismatch", /Contract financing terms do not match/.test(flagInfo.text), "");
    check("Matt: flags include register C8", /Conflicts register C8/.test(flagInfo.text), "");
    await page.screenshot({ path: path.join(OUT, "matt-flags.png"), fullPage: true });
    await axeRun(page, "matt-flags", axeResults);

    // Preview as Carole
    await page.selectOption("#preview-select", "carole");
    await page.waitForTimeout(400);
    const prev = await page.evaluate(() => ({
      banner: !document.getElementById("preview-banner").hidden,
      bannerText: document.getElementById("preview-banner-text").textContent,
      lendersHidden: document.getElementById("tab-lenders").hidden,
      steps: document.querySelectorAll("#steps .xitem").length,
      flagRows: document.querySelectorAll("#flags li").length,
      bodyHasCurrency: /\$\s?\d/.test(document.body.innerText),
      viewer: document.getElementById("viewername").textContent,
    }));
    check("Preview Carole: banner shows", prev.banner && /Previewing as Carole Kirchner/.test(prev.bannerText), prev.bannerText.slice(0, 80));
    check("Preview Carole: lenders tab hidden", prev.lendersHidden === true, "");
    check("Preview Carole: steps sliced to 2", prev.steps === 2, "steps=" + prev.steps);
    check("Preview Carole: flags sliced to 2", prev.flagRows === 2, "flags=" + prev.flagRows);
    check("Preview Carole: no currency anywhere in the DOM", prev.bodyHasCurrency === false, "");
    check("Preview Carole: header shows Carole", prev.viewer === "Carole Kirchner", prev.viewer);
    await page.screenshot({ path: path.join(OUT, "matt-preview-carole.png"), fullPage: true });
    await axeRun(page, "preview-carole", axeResults);
    await page.click("#preview-exit");
    await page.waitForTimeout(300);
    const back = await page.evaluate(() => ({
      lendersHidden: document.getElementById("tab-lenders").hidden,
      banner: document.getElementById("preview-banner").hidden,
    }));
    check("Preview exit restores Matt's full view", back.lendersHidden === false && back.banner === true, "");
    await page.click("#signout");
    await page.waitForSelector("#signedout:not([hidden])", { timeout: 20000 });
    await ctx.close();
  }

  // ---------- Carole (real session) ----------
  for (const vp of [{ name: "desktop", width: 1280, height: 900 }, { name: "mobile", width: 390, height: 844 }]) {
    const ctx = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: vp.width, height: vp.height } });
    const page = await ctx.newPage();
    await signInFlow(page, byName["Carole Kirchner"]);
    const info = await page.evaluate(() => ({
      steps: document.querySelectorAll("#steps .xitem").length,
      flagRows: document.querySelectorAll("#flags li").length,
      flagText: document.getElementById("flags").textContent,
      bodyHasCurrency: /\$\s?\d/.test(document.body.innerText),
      lendersHidden: document.getElementById("tab-lenders").hidden,
      tlPoints: document.querySelectorAll(".tl-point").length,
    }));
    check(`Carole ${vp.name}: slice steps=2`, info.steps === 2, "steps=" + info.steps);
    check(`Carole ${vp.name}: flags=2 (amendment + register C7)`, info.flagRows === 2 && /Contract amendment pending/.test(info.flagText), "flags=" + info.flagRows);
    check(`Carole ${vp.name}: no currency in DOM`, info.bodyHasCurrency === false, "");
    check(`Carole ${vp.name}: no lenders tab`, info.lendersHidden === true, "");
    check(`Carole ${vp.name}: timeline shows all 6 deadlines`, info.tlPoints === 6, "points=" + info.tlPoints);
    if (vp.name === "mobile") {
      const barBox = await page.evaluate(() => {
        const wrap = document.querySelector(".tabwrap");
        const bar = document.querySelector(".tabbar");
        const wr = wrap.getBoundingClientRect();
        const br = bar.getBoundingClientRect();
        return { wrapBottom: Math.round(wr.bottom), wrapHeight: Math.round(wr.height), barBottom: Math.round(br.bottom), vh: window.innerHeight, pos: getComputedStyle(wrap).position };
      });
      check("Carole mobile: tab bar fixed at bottom (thumb zone)",
        barBox.pos === "fixed" && Math.abs(barBox.wrapBottom - barBox.vh) <= 2 && Math.abs(barBox.barBottom - barBox.vh) <= 14 && barBox.wrapHeight < 120,
        JSON.stringify(barBox));
    }
    await page.click("#tab-deadlines");
    await page.waitForTimeout(300);
    await axeRun(page, "carole-" + vp.name + "-deadlines", axeResults);
    await page.click("#tab-flags");
    await page.waitForTimeout(200);
    await axeRun(page, "carole-" + vp.name + "-flags", axeResults);
    await page.screenshot({ path: path.join(OUT, `carole-${vp.name}-flags.png`), fullPage: true });
    await page.click("#signout");
    await page.waitForSelector("#signedout:not([hidden])", { timeout: 20000 });
    await ctx.close();
  }

  await browser.close();
  relay.close();
  const failed = results.filter((r) => !r.ok);
  fs.writeFileSync(path.join(OUT, "verification-results.json"), JSON.stringify({ results, axeResults }, null, 1));
  console.log("TOTAL:", results.length, "checks,", failed.length, "failed");
  if (failed.length) process.exit(2);
})().catch((e) => {
  console.error("FAILED:", e.message);
  try {
    fs.mkdirSync(OUT, { recursive: true });
    fs.writeFileSync(path.join(OUT, "verification-results.json"), JSON.stringify({ results, error: e.message }, null, 1));
  } catch (_) {}
  process.exit(1);
});
