/* AegisForge.app -- the page.
 *
 * READS:   fetch("/api/...") against the loopback server (GET only).
 * ACTIONS: window.pywebview.api.<method>(...) -- pywebview's js_api bridge,
 *          which exists only inside the native window. Every mutating call
 *          is made from a funnel's confirm step, never on load or a timer.
 *
 * DOM safety: nothing engine-provided ever reaches an HTML-string sink. Every
 * node is built with h() (text via textContent), so a hostile process name or
 * alert line is inert. The test suite asserts no such sink exists in this file.
 *
 * WKWebView (macOS 26) returns js_api results as JSON *strings*: api() parses.
 */
"use strict";

// ── tiny DOM helper ─────────────────────────────────────────────────────
function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, String(v));
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    el.appendChild(typeof kid === "string" || typeof kid === "number" ? document.createTextNode(String(kid)) : kid);
  }
  return el;
}
const $ = (id) => document.getElementById(id);
function clear(el) { el.replaceChildren(); return el; }
function show(el, ...kids) { clear(el); for (const k of kids.flat()) if (k != null) el.appendChild(typeof k === "string" ? document.createTextNode(k) : k); return el; }
const NS = "http://www.w3.org/2000/svg";
function svg(tag, attrs) { const e = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, String(v)); return e; }

// ── icons (inline SVG paths; nav + inline marks) ────────────────────────
const ICONS = {
  pulse: "M2 8h3l2-5 3 10 2-5h4", cpu: "M5 5h6v6H5z M2 6h3M2 10h3M11 6h3M11 10h3M6 2v3M10 2v3M6 11v3M10 11v3",
  broom: "M11 2 7 6 M7 6l3 3 M4 9l3-3 3 3-3 5H4z", shield: "M8 2 13 4v4c0 3-2.5 5-5 6-2.5-1-5-3-5-6V4z",
  box: "M2 5l6-3 6 3v6l-6 3-6-3z M2 5l6 3 6-3 M8 8v6", disk: "M8 2a6 6 0 1 0 0 12A6 6 0 0 0 8 2z M8 6.5a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3z",
  ember: "M8 2c1 2 4 3.5 4 7a4 4 0 0 1-8 0c0-2 1-3 1.5-3.5C6 7 7 7.5 7 8.5 7.5 7 8 5 8 2z", check: "M3 8.5l3 3 7-7", x: "M4 4l8 8M12 4l-8 8", eye: "M2 8s2-4 6-4 6 4 6 4-2 4-6 4-6-4-6-4z M8 8m-1.5 0a1.5 1.5 0 1 0 3 0 1.5 1.5 0 1 0-3 0",
};
function icon(name) { const s = svg("svg", { viewBox: "0 0 16 16" }); s.appendChild(svg("path", { d: ICONS[name] || "" })); return s; }
document.querySelectorAll(".nav-ico").forEach((el) => el.appendChild(icon(el.dataset.ico)));

// ── formatting ──────────────────────────────────────────────────────────
function fmtBytes(n) { n = Number(n) || 0; const u = ["B", "KB", "MB", "GB", "TB"]; let i = 0; while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${u[i]}`; }
function fmtAge(s) { if (s == null) return "?"; if (s < 60) return `${s}s`; if (s < 3600) return `${Math.floor(s / 60)}m`; if (s < 86400) return `${Math.floor(s / 3600)}h`; return `${Math.floor(s / 86400)}d`; }
function num(v, d = 1) { return v == null || Number.isNaN(Number(v)) ? "--" : Number(v).toFixed(d); }
const SEV_RANK = { critical: 4, high: 3, medium: 2, low: 1, info: 0, ok: -1 };

// ── the bridge (js_api) ─────────────────────────────────────────────────
const bridge = { ready: false, checked: false };
function setBridge(on, why) {
  bridge.ready = on; bridge.checked = true;
  const b = $("bridge"); b.className = "bridge " + (on ? "is-on" : "is-off");
  show(b, h("i"), h("span", null, on ? "bridge: native window" : `bridge: ${why || "read-only tab"}`));
  document.querySelectorAll("[data-needs-bridge]").forEach((el) => { el.disabled = !on; el.title = on ? "" : "Actions need the native AegisForge window (this tab is read-only)."; });
}
async function api(name, ...args) {
  if (!window.pywebview || !window.pywebview.api) throw new Error("native bridge unavailable: this is a read-only tab. Open AegisForge.app for actions.");
  const fn = window.pywebview.api[name];
  if (typeof fn !== "function") throw new Error(`bridge has no method ${name}`);
  let r = await fn(...args);
  if (typeof r === "string") { try { r = JSON.parse(r); } catch (e) { /* a plain string result stays a string */ } }
  if (r && typeof r === "object" && !("ok" in r)) r.ok = true;
  return r;
}
async function probeBridge() {
  if (window.pywebview && window.pywebview.api) {
    try { const r = await api("ping"); if (r && r.ok) { setBridge(true); return; } } catch (e) { /* fall through */ }
  }
  setBridge(false);
}
window.addEventListener("pywebviewready", probeBridge);
setTimeout(() => { if (!bridge.checked) probeBridge(); }, 1800);
async function get(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) { let m = `${r.status}`; try { m = (await r.json()).error || m; } catch (e) { /* text */ } throw new Error(m); }
  return r.json();
}

// ── toasts + confirm modal ──────────────────────────────────────────────
function toast(msg, kind = "ok", ms = 4200) {
  const t = h("div", { class: `toast t-${kind}` }, msg);
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), ms);
}
/** confirm({title, body: [nodes], ok, ack}) -> Promise<{ok, acked}>. With `ack`
 *  the Confirm button stays disabled until the acknowledgement is ticked. */
function confirmModal({ title, body, ok = "Confirm", ack = null, danger = true }) {
  return new Promise((resolve) => {
    const back = $("modal"), okBtn = $("modal-ok"), cancel = $("modal-cancel"), ackRow = $("modal-ack"), ackBox = $("modal-ack-box");
    show($("modal-title"), title);
    show($("modal-body"), ...(Array.isArray(body) ? body : [body]));
    okBtn.textContent = ok; okBtn.className = danger ? "btn btn-danger is-solid" : "btn btn-primary";
    ackBox.checked = false;
    if (ack) { show($("modal-ack-text"), ack); ackRow.hidden = false; okBtn.disabled = true; } else { ackRow.hidden = true; okBtn.disabled = false; }
    const onAck = () => { okBtn.disabled = !ackBox.checked; };
    const done = (v) => { back.hidden = true; okBtn.removeEventListener("click", onOk); cancel.removeEventListener("click", onNo); ackBox.removeEventListener("change", onAck); document.removeEventListener("keydown", onKey); resolve({ ok: v, acked: !!ack && ackBox.checked }); };
    const onOk = () => done(true), onNo = () => done(false);
    const onKey = (e) => { if (e.key === "Escape") done(false); };
    okBtn.addEventListener("click", onOk); cancel.addEventListener("click", onNo); ackBox.addEventListener("change", onAck); document.addEventListener("keydown", onKey);
    back.hidden = false; (ack ? ackBox : okBtn).focus();
  });
}
function kvRows(pairs) { return h("div", { class: "kv" }, pairs.filter((p) => p[1] != null && p[1] !== "").map(([k, v]) => h("div", null, h("span", null, k), h("span", null, String(v))))); }

// ── navigation ──────────────────────────────────────────────────────────
const views = {};
let current = "overview";
function go(name) {
  current = name;
  document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.view === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("is-active", v.dataset.view === name));
  document.querySelector(".content").scrollTop = 0;   // never scrollIntoView: it drags the whole grid
  if (views[name] && views[name].enter) views[name].enter();
  try { localStorage.setItem("af.view", name); } catch (e) { /* private mode */ }
}
$("nav").addEventListener("click", (e) => { const b = e.target.closest(".nav-item"); if (b) go(b.dataset.view); });

// ── vitals + sparklines (top bar + overview) ────────────────────────────
const VITALS = { cpu: ["CPU", "%", 100, 1], ram: ["RAM", "%", 100, 1], swap_gb: ["Swap", "GB", 32, 1], load1: ["Load", "", 16, 2], disk_free_gb: ["Disk free", "GB", 512, 0] };
function tone(id, v) {
  v = Number(v) || 0;
  if (id === "swap_gb") return v > 16 ? "bad" : v > 8 ? "ember" : v > 4 ? "amber" : "";
  if (id === "ram") return v > 92 ? "bad" : v > 85 ? "ember" : v > 72 ? "amber" : "";
  if (id === "cpu") return v > 90 ? "ember" : v > 70 ? "amber" : "";
  if (id === "load1") return v > 16 ? "ember" : v > 10 ? "amber" : "";
  if (id === "disk_free_gb") return v < 5 ? "bad" : v < 15 ? "amber" : "";
  return "";
}
function pct(v, max) { return Math.max(0, Math.min(100, (Number(v) || 0) / max * 100)); }
function sparkline(vals, tn) {
  const w = 120, hh = 30, s = svg("svg", { class: "spark " + (tn ? "tone-" + tn : ""), viewBox: `0 0 ${w} ${hh}`, preserveAspectRatio: "none" });
  const v = vals.filter((x) => x != null && !Number.isNaN(Number(x))).map(Number);
  if (v.length < 2) return s;
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((y, i) => [i / (v.length - 1) * w, hh - 2 - (y - lo) / span * (hh - 6)]);
  s.appendChild(svg("polygon", { class: "fill", points: [[0, hh], ...pts, [w, hh]].map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ") }));
  s.appendChild(svg("polyline", { points: pts.map((p) => p.map((n) => n.toFixed(1)).join(",")).join(" ") }));
  return s;
}
let history = [];
function renderVitals(vitals) {
  const grid = clear($("vitals"));
  for (const [id, [label, unit, max, dec]] of Object.entries(VITALS)) {
    const val = vitals[id], tn = tone(id, val);
    const bar = h("div", { class: "bar " + (tn ? "tone-" + tn : "") }, h("i", { style: { width: pct(val, max) + "%" } }));
    grid.appendChild(h("div", { class: "vital" }, h("div", { class: "k" }, label), h("div", { class: "v" }, num(val, dec), unit ? h("small", null, unit) : null), bar, sparkline(history.map((r) => r[id]), tn)));
  }
  const chips = clear($("chips"));
  for (const [id, [label, unit, , dec]] of Object.entries(VITALS)) chips.appendChild(h("span", { class: "chip" }, label, " ", h("b", null, num(vitals[id], dec) + (unit ? " " + unit : ""))));
}
function findingNode(f) { return h("div", { class: "finding sev-" + f.severity }, h("span", { class: "lvl" }, f.severity), h("span", null, f.text)); }
async function tick() {
  let d; try { d = await get("/api/status"); } catch (e) { return; }
  const age = $("age"); age.textContent = d.age_label || ""; age.classList.toggle("is-stale", !!d.stale);
  const worst = $("worst"); worst.className = "worst sev-" + (d.worst || "ok");
  show(worst, h("i"), h("span", null, d.worst === "ok" ? "all clear" : `worst: ${d.worst}`));
  renderVitals(d.vitals || {});
  const F = clear($("findings"));
  if (d.error) F.appendChild(h("div", { class: "finding sev-medium" }, h("span", { class: "lvl" }, "engine"), h("span", null, String(d.error))));
  if (!d.findings || !d.findings.length) F.appendChild(h("div", { class: "calm" }, icon("check"), "All clear -- forge cold."));
  else for (const f of [...d.findings].sort((a, b) => (SEV_RANK[b.severity] || 0) - (SEV_RANK[a.severity] || 0))) F.appendChild(findingNode(f));
  const A = clear($("alerts"));
  if (!d.alerts || !d.alerts.length) A.appendChild(h("div", { class: "muted" }, "none"));
  else for (const x of d.alerts) A.appendChild(h("div", { class: "alert-line" }, x));
}
async function pullHistory() { try { history = (await get("/api/history?n=60")).rows || []; $("forge-note").textContent = history.length ? `${history.length} samples` : ""; } catch (e) { /* keep old */ } }
function ring(score) {
  const r = 36, c = 2 * Math.PI * r, tn = score >= 80 ? "" : score >= 50 ? "amber" : "bad";
  const s = svg("svg", { viewBox: "0 0 86 86" });
  s.appendChild(svg("circle", { class: "track", cx: 43, cy: 43, r }));
  s.appendChild(svg("circle", { class: "arc", cx: 43, cy: 43, r, "stroke-dasharray": c.toFixed(1), "stroke-dashoffset": (c * (1 - score / 100)).toFixed(1) }));
  return h("div", { class: "ring " + (tn ? "tone-" + tn : "") }, s, h("div", { class: "val" }, String(score), h("small", null, "/100")));
}
async function loadHealth(force) {
  const box = $("health");
  if (!force && box.dataset.at && Date.now() - Number(box.dataset.at) < 5 * 60e3) return;
  show(box, h("div", { class: "working" }, h("span", { class: "spinner" }), "running health checks..."));
  try {
    const d = await get("/api/health"); box.dataset.at = String(Date.now());
    const checks = [...(d.checks || [])].sort((a, b) => ({ fail: 0, warn: 1, pass: 2 }[a.status] ?? 3) - ({ fail: 0, warn: 1, pass: 2 }[b.status] ?? 3));
    show(box, ring(d.score), h("div", { class: "checks" }, checks.slice(0, 9).map((c) => h("div", { class: "check" }, h("span", { class: "st st-" + c.status }, c.status), h("span", { class: "nm" }, c.name), h("span", { class: "dt" }, c.detail))),
      checks.length > 9 ? h("div", { class: "muted", style: { fontSize: "11px" } }, `+${checks.length - 9} more (macmon health)`) : null));
  } catch (e) { show(box, h("div", { class: "result-bad" }, "health check failed: " + e.message)); }
}
$("health-refresh").addEventListener("click", () => loadHealth(true));
views.overview = { enter() { tick(); loadHealth(false); } };

// ── Processes: list + kill / suspend / resume funnels ───────────────────
const ps = { rows: [], busy: false, timer: null };
function catBadge(c) { return h("span", { class: "badge " + ({ ide: "badge-sky", llm: "badge-ember", docker: "badge-sky", browser: "" }[c] || "") }, c); }
function renderProcs() {
  const q = ($("ps-search").value || "").trim().toLowerCase();
  const rows = ps.rows.filter((p) => !q || p.name.toLowerCase().includes(q) || String(p.pid) === q || p.category.includes(q));
  const tb = clear($("ps-rows"));
  if (!rows.length) { tb.appendChild(h("tr", null, h("td", { colspan: 7, class: "muted" }, ps.rows.length ? "no match" : "no processes"))); return; }
  for (const p of rows.slice(0, 120)) {
    const stopped = p.status === "stopped";
    const acts = h("td", { class: "actions" });
    if (p.protected) acts.appendChild(h("span", { class: "badge" }, "protected"));
    else {
      acts.appendChild(h("button", { class: "btn btn-xs btn-ghost", type: "button", disabled: !bridge.ready, onclick: () => signalFunnel(stopped ? "resume" : "suspend", p) }, stopped ? "Resume" : "Suspend"));
      acts.appendChild(h("button", { class: "btn btn-xs btn-danger", type: "button", disabled: !bridge.ready, onclick: () => signalFunnel("kill", p) }, "Kill"));
    }
    tb.appendChild(h("tr", { class: p.protected ? "is-protected" : "" },
      h("td", { class: "name", title: p.name }, p.name), h("td", { class: "num mono" }, String(p.pid)),
      h("td", { class: "num " + (p.cpu > 90 ? "hot" : p.cpu > 50 ? "warm" : "") }, num(p.cpu, 1)),
      h("td", { class: "num" }, p.ram_label), h("td", null, catBadge(p.category)),
      h("td", { class: stopped ? "warm" : "" }, p.status, p.age_s != null ? h("span", { class: "muted" }, ` · ${fmtAge(p.age_s)}`) : null), acts));
  }
  $("ps-note").textContent = `${rows.length} shown of ${ps.rows.length} listed (${ps.total || ps.rows.length} matched the dev filter). Sorted by ${ps.sort}. Auto-refresh 15s.`;
}
async function loadProcs() {
  if (ps.busy) return; ps.busy = true;
  try { const d = await get("/api/processes?sort=" + encodeURIComponent($("ps-sort").value)); ps.rows = d.processes || []; ps.total = d.total; ps.sort = d.sort; renderProcs(); }
  catch (e) { show($("ps-rows"), h("tr", null, h("td", { colspan: 7, class: "result-bad" }, "could not list processes: " + e.message))); }
  finally { ps.busy = false; }
}
$("ps-search").addEventListener("input", renderProcs);
$("ps-sort").addEventListener("change", loadProcs);
$("ps-refresh").addEventListener("click", loadProcs);
/** The signal funnel: guard check -> confirm (with acknowledgement when the
 *  sweep-style guard names it) -> bridge call -> toast + refresh. */
async function signalFunnel(verb, p) {
  let g;
  try { g = await api("process_guard", p.pid, p.created); } catch (e) { toast(e.message, "bad"); return; }
  if (!g.ok) { toast(g.detail || "refused", "bad"); loadProcs(); return; }
  const VERB = { kill: ["Kill", "Sends SIGTERM (graceful, never SIGKILL). The process may save state and exit; a stuck one may ignore it."], suspend: ["Suspend", "Sends SIGSTOP: the process freezes (and its windows stop responding) until you Resume it."], resume: ["Resume", "Sends SIGCONT: the process continues where it was frozen."] }[verb];
  const body = [kvRows([["process", g.name], ["pid", g.pid], ["user", g.user], ["command", g.cmd]]), h("div", { class: "info" }, VERB[1])];
  if (g.in_service === true) body.push(h("div", { class: "warn" }, "This process (or a child) has an open or listening socket: something may be talking to it right now."));
  if (g.guard) body.push(h("div", { class: "warn" }, `Guarded (${g.guard}): AegisForge would never touch this on its own -- it looks like a live workload, an agent, the fleet or a user app.`));
  const { ok, acked } = await confirmModal({ title: `${VERB[0]} ${g.name} (PID ${g.pid})?`, body, ok: VERB[0], danger: verb !== "resume", ack: g.guard ? "I understand: I am doing this by hand, on a guarded process." : null });
  if (!ok) return;
  toast(`${VERB[0]}ing ${g.name}...`, "warn", 1500);
  try {
    const r = await api(`${verb}_process`, g.pid, p.created, acked);
    toast(r.detail || (r.ok ? "done" : "refused"), r.ok ? (verb === "kill" ? "ember" : "ok") : "bad");
  } catch (e) { toast(e.message, "bad"); }
  setTimeout(loadProcs, 900);
}
$("ps-purge").addEventListener("click", async () => {
  const { ok } = await confirmModal({ title: "Purge inactive RAM?", ok: "Purge", danger: false, body: [h("p", null, "Runs ", h("code", null, "sudo -n purge"), ". Non-destructive: it flushes inactive memory and the disk cache (apps may feel cold for a moment). It never prompts -- it fails fast unless ", h("code", null, "macmon sentinel --setup-purge"), " installed the sudoers rule.")] });
  if (!ok) return;
  try { const r = await api("purge_ram"); toast(r.detail || "done", r.ok ? "ok" : "bad", 6000); } catch (e) { toast(e.message, "bad"); }
});
views.processes = { enter() { loadProcs(); clearInterval(ps.timer); ps.timer = setInterval(() => { if (current === "processes" && $("modal").hidden) loadProcs(); }, 15000); } };

// ── Clean: scan -> review -> confirm -> run -> done ─────────────────────
const clean = { state: "idle", scan: null, picked: new Set(), result: null, error: null };
function stepper(el, idx, doneAll) { [...el.children].forEach((li, i) => { li.classList.toggle("is-current", i === idx && !doneAll); li.classList.toggle("is-done", i < idx || doneAll); }); }
function renderClean() {
  const st = clear($("clean-stage")); st.className = "card stage";
  const s = clean.state;
  if (s === "idle" || s === "error") {
    stepper($("clean-stepper"), 0);
    st.appendChild(h("div", { class: "stage-hero" }, h("h3", null, "Scan for junk"), h("p", null, "Old logs, crash reports, stale temp files, browser caches (cache + crash reports only -- never cookies, history or logins), app caches and the biggest user caches. The scan touches nothing."),
      clean.error ? h("div", { class: "result-bad" }, clean.error) : null,
      h("button", { class: "btn btn-primary", type: "button", onclick: cleanScan }, "Scan")));
    if (!bridge.ready) st.appendChild(h("div", { class: "ro-banner" }, icon("eye"), "Read-only tab: the clean funnel runs inside the native AegisForge window."));
  } else if (s === "scanning") {
    stepper($("clean-stepper"), 0);
    st.appendChild(h("div", { class: "working" }, h("span", { class: "spinner" }), "scanning system junk, browser, app and user caches..."));
  } else if (s === "review") {
    stepper($("clean-stepper"), 1);
    const cats = clean.scan.categories;
    if (!cats.length) { st.appendChild(h("div", { class: "calm" }, icon("check"), "Nothing to clean -- the system is already clean.")); st.appendChild(h("button", { class: "btn btn-ghost", type: "button", onclick: () => { clean.state = "idle"; renderClean(); } }, "Back")); return; }
    const picks = h("div", { class: "picks" }, cats.map((c) => h("label", { class: "pick" }, h("input", { type: "checkbox", checked: clean.picked.has(c.id), onchange: (e) => { if (e.target.checked) clean.picked.add(c.id); else clean.picked.delete(c.id); renderClean(); } }), h("span", { class: "pk-name" }, c.name), h("span", { class: "pk-count" }, `${c.count} item${c.count === 1 ? "" : "s"}`), h("span", { class: "pk-size" }, c.size_label))));
    const sel = cats.filter((c) => clean.picked.has(c.id)), selBytes = sel.reduce((a, c) => a + c.size, 0);
    st.appendChild(h("div", { class: "stage-hero" }, h("h3", null, "Review what goes to the Trash"), h("p", null, `Found ${clean.scan.total_label} across ${cats.length} categories. Untick anything you want to keep.`)));
    st.appendChild(picks);
    if (clean.scan.notes && clean.scan.notes.length) st.appendChild(h("div", { class: "notes" }, clean.scan.notes.join("\n")));
    st.appendChild(h("div", { class: "summary" }, h("span", { class: "big" }, fmtBytes(selBytes), h("small", null, `selected in ${sel.length} categor${sel.length === 1 ? "y" : "ies"}`)), h("span", { class: "grow" }),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => { clean.picked = new Set(cats.map((c) => c.id)); renderClean(); } }, "All"),
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => { clean.picked.clear(); renderClean(); } }, "None"),
      h("button", { class: "btn btn-danger is-solid", type: "button", disabled: !sel.length, onclick: cleanConfirm }, `Clean ${fmtBytes(selBytes)}`)));
  } else if (s === "running") {
    stepper($("clean-stepper"), 2);
    st.appendChild(h("div", { class: "working" }, h("span", { class: "spinner" }), "moving to the Trash..."));
  } else if (s === "done") {
    stepper($("clean-stepper"), 3, true);
    const r = clean.result;
    st.appendChild(h("div", { class: "stage-hero" }, h("h3", { class: "result-ok" }, "Cleaned"), h("div", { class: "big" }, `freed ${r.freed_label}`, h("small", null, `${r.cleaned.length} categor${r.cleaned.length === 1 ? "y" : "ies"} -> Trash`)),
      h("p", null, r.cleaned.join(" · ")), r.skipped ? h("div", { class: "warn muted" }, `${r.skipped} path(s) skipped (Trash refused them; nothing was force-deleted).`) : null,
      r.notes && r.notes.length ? h("div", { class: "notes" }, r.notes.join("\n")) : null,
      h("button", { class: "btn btn-ghost", type: "button", onclick: () => { clean.state = "idle"; clean.scan = null; renderClean(); } }, "Scan again")));
  }
}
async function cleanScan() {
  clean.state = "scanning"; clean.error = null; renderClean();
  try { const r = await api("clean_scan"); if (!r.ok) throw new Error(r.detail || "scan failed"); clean.scan = r; clean.picked = new Set(r.categories.map((c) => c.id)); clean.state = "review"; }
  catch (e) { clean.state = "error"; clean.error = e.message; }
  renderClean();
}
async function cleanConfirm() {
  const cats = clean.scan.categories.filter((c) => clean.picked.has(c.id)), bytes = cats.reduce((a, c) => a + c.size, 0);
  const { ok } = await confirmModal({ title: `Move ${fmtBytes(bytes)} to the Trash?`, ok: "Clean", body: [h("p", null, `${cats.length} categor${cats.length === 1 ? "y" : "ies"}: ${cats.map((c) => c.name).join(", ")}.`), h("div", { class: "info" }, "Trash-first: files go to the Trash (recoverable). Anything the Trash refuses is skipped -- never deleted permanently.")] });
  if (!ok) return;
  clean.state = "running"; renderClean();
  try { const r = await api("clean_execute", cats.map((c) => c.id)); if (!r.ok) throw new Error(r.detail || "clean refused"); clean.result = r; clean.state = "done"; toast(`Freed ${r.freed_label}`, "ok"); }
  catch (e) { clean.state = "error"; clean.error = e.message; toast(e.message, "bad"); }
  renderClean();
}
views.clean = { enter() { renderClean(); } };

// ── Security: scan -> scored findings; quarantine funnel ────────────────
const sec = { busy: false };
async function secScan() {
  if (sec.busy) return; sec.busy = true; $("sec-scan").disabled = true;
  show($("sec-result"), h("div", { class: "card" }, h("div", { class: "working" }, h("span", { class: "spinner" }), "firewall, SIP, Gatekeeper, FileVault, connections, remote tools, processes, startup items, sharing, SSH...")));
  try {
    const d = await get("/api/security");
    const order = { fail: 0, warn: 1, pass: 2 };
    const items = [...(d.findings || [])].sort((a, b) => (order[a.status] ?? 3) - (order[b.status] ?? 3));
    const fails = items.filter((f) => f.status === "fail").length, warns = items.filter((f) => f.status === "warn").length;
    show($("sec-result"), h("div", { class: "card" },
      h("div", { class: "score-row" }, ring(d.score), h("div", null, h("div", { class: "big" }, `${d.score}`, h("small", null, "/100 security score")), h("div", { class: "muted" }, `${fails} fail · ${warns} warn · ${items.length - fails - warns} pass`))),
      h("div", { class: "sec-list" }, items.map((f) => h("div", { class: "sec-item st-" + f.status },
        h("div", { class: "row" }, h("span", { class: "badge badge-" + ({ pass: "ok", warn: "warn", fail: "fail" }[f.status] || "") }, f.status), h("span", { class: "nm" }, f.name), h("span", { class: "dt" }, f.detail)),
        f.status !== "pass" && f.fix_hint ? h("div", { class: "hint" }, f.fix_hint) : null,
        f.items && f.items.length ? h("ul", null, f.items.slice(0, 12).map((x) => h("li", null, String(x)))) : null)))));
    $("sec-note").textContent = `scanned ${new Date().toLocaleTimeString()}`;
  } catch (e) { show($("sec-result"), h("div", { class: "card result-bad" }, "scan failed: " + e.message)); }
  finally { sec.busy = false; $("sec-scan").disabled = false; }
}
$("sec-scan").addEventListener("click", secScan);
$("q-go").addEventListener("click", async () => {
  const pid = parseInt($("q-pid").value, 10);
  if (!pid) { $("q-note").textContent = "enter a PID"; return; }
  let g; try { g = await api("process_guard", pid); } catch (e) { toast(e.message, "bad"); return; }
  if (!g.ok) { $("q-note").textContent = g.detail || "refused"; toast(g.detail || "refused", "bad"); return; }
  const body = [kvRows([["process", g.name], ["pid", g.pid], ["user", g.user], ["command", g.cmd]]), h("div", { class: "warn" }, "Kills the process (SIGTERM, then SIGKILL if it survives 1s) and adds its binary to the application firewall's block list (needs sudo; inbound only). The firewall step reports honestly if sudo needs a password.")];
  if (g.guard) body.push(h("div", { class: "warn" }, `Guarded (${g.guard}): AegisForge would never touch this on its own.`));
  const { ok } = await confirmModal({ title: `Quarantine ${g.name} (PID ${g.pid})?`, ok: "Quarantine", body, ack: "I understand this kills the process and blocks its binary." });
  if (!ok) return;
  try { const r = await api("quarantine", g.pid); $("q-note").textContent = r.detail || ""; toast(r.detail || (r.ok ? "quarantined" : "refused"), r.ok ? "ember" : "bad", 6000); if (r.lines) show($("q-note"), h("span", { class: "notes" }, r.lines.join("\n"))); } catch (e) { toast(e.message, "bad"); }
});
views.security = { enter() { /* on demand: the scan is explicit */ } };

// ── Docker: overview, tabs, guarded prune ───────────────────────────────
const dk = { data: null, tab: "containers", busy: false };
function dkTable(cols, rows, cell) {
  if (!rows.length) return h("div", { class: "pad muted" }, "none");
  return h("table", { class: "table" }, h("thead", null, h("tr", null, cols.map((c) => h("th", { class: c.num ? "num" : "" }, c.label)))), h("tbody", null, rows.map((r) => h("tr", null, cols.map((c) => h("td", { class: (c.num ? "num " : "") + (c.cls ? c.cls(r) : ""), title: String(cell(r, c.key) ?? "") }, cell(r, c.key)))))));
}
function renderDocker() {
  const d = dk.data, ov = clear($("dk-overview")), panel = clear($("dk-panel"));
  if (!d) return;
  if (!d.available) { ov.appendChild(h("div", { class: "stat", style: { gridColumn: "1 / -1" } }, h("div", { class: "k" }, "docker"), h("div", { class: "v muted" }, "not running / not installed"))); $("dk-prune").disabled = true; return; }
  const o = d.overview || {}, dangling = o.dangling_images || 0;
  [["running", (o.running || []).length], ["stopped", (o.stopped || []).length], ["dangling images", dangling, dangling ? "warm" : ""], ["volumes", o.volumes || 0]].forEach(([k, v, cls]) => ov.appendChild(h("div", { class: "stat" }, h("div", { class: "k" }, k), h("div", { class: "v " + (cls || "") }, String(v)))));
  $("dk-prune").disabled = !bridge.ready || !dangling; $("dk-prune").textContent = `Prune dangling images${dangling ? ` (${dangling})` : ""}`;
  document.querySelectorAll("#dk-tabs .tab").forEach((t) => t.classList.toggle("is-active", t.dataset.tab === dk.tab));
  const val = (r, k) => r[k] == null ? "" : String(r[k]);
  if (dk.tab === "containers") panel.appendChild(dkTable([{ key: "name", label: "Name" }, { key: "image", label: "Image" }, { key: "status", label: "Status", cls: (r) => r.state === "running" ? "result-ok" : r.state === "exited" ? "warm" : "" }, { key: "ports", label: "Ports" }, { key: "size", label: "Size", num: true }], d.containers || [], val));
  else if (dk.tab === "images") panel.appendChild(dkTable([{ key: "repository", label: "Repository", cls: (r) => r.repository === "<none>" ? "warm" : "" }, { key: "tag", label: "Tag" }, { key: "id", label: "ID" }, { key: "created", label: "Created" }, { key: "size", label: "Size", num: true }], d.images || [], val));
  else if (dk.tab === "volumes") panel.appendChild(dkTable([{ key: "name", label: "Name" }, { key: "driver", label: "Driver" }, { key: "in_use", label: "In use", cls: (r) => r.in_use ? "result-ok" : "muted" }], d.volumes || [], (r, k) => k === "in_use" ? (r.in_use ? "yes" : "no") : val(r, k)));
  else panel.appendChild(dkTable([{ key: "type", label: "Type" }, { key: "total", label: "Total", num: true }, { key: "active", label: "Active", num: true }, { key: "size", label: "Size", num: true }, { key: "reclaimable", label: "Reclaimable", num: true }], o.disk_usage || [], val));
}
async function loadDocker() {
  if (dk.busy) return; dk.busy = true;
  if (!dk.data) show($("dk-panel"), h("div", { class: "pad working" }, h("span", { class: "spinner" }), "asking docker..."));
  try { dk.data = await get("/api/docker"); renderDocker(); } catch (e) { show($("dk-panel"), h("div", { class: "pad result-bad" }, "docker: " + e.message)); } finally { dk.busy = false; }
}
$("dk-tabs").addEventListener("click", (e) => { const t = e.target.closest(".tab"); if (t) { dk.tab = t.dataset.tab; renderDocker(); } });
$("dk-refresh").addEventListener("click", loadDocker);
$("dk-prune").addEventListener("click", async () => {
  const n = (dk.data && dk.data.overview && dk.data.overview.dangling_images) || 0;
  const { ok } = await confirmModal({ title: `Prune ${n} dangling image${n === 1 ? "" : "s"}?`, ok: "Prune", body: [h("p", null, "Runs ", h("code", null, "docker image prune -f"), " -- dangling (untagged, unreferenced) images only."), h("div", { class: "info" }, "Never -a, never containers, volumes, build cache or networks. Those stay in macmon docker --prune, where you confirm them in the terminal.")] });
  if (!ok) return;
  $("dk-prune").disabled = true; toast("pruning...", "warn", 1500);
  try { const r = await api("docker_prune_dangling"); toast(r.detail || (r.ok ? "pruned" : "refused"), r.ok ? "ok" : "bad", 6000); } catch (e) { toast(e.message, "bad"); }
  dk.data = null; loadDocker();
});
views.docker = { enter() { loadDocker(); } };

// ── Disk: usage + big files (+ reveal) ──────────────────────────────────
async function loadDisk() {
  const p = $("disk-path").value.trim() || "~", box = $("disk-usage");
  show(box, h("div", { class: "working" }, h("span", { class: "spinner" }), `sizing ${p} ...`));
  try {
    const d = await get("/api/disk?path=" + encodeURIComponent(p));
    if (d.error) { show(box, h("div", { class: "result-bad" }, d.error)); return; }
    const top = Math.max(...d.entries.map((e) => e.size), 1);
    show(box, h("div", { class: "muted", style: { marginBottom: "6px" } }, `${d.path} -- ${d.total_label} in ${d.entries.length} top-level entries`),
      d.entries.map((e) => h("div", { class: "use-row" }, h("span", { class: "nm", title: e.path }, e.name), h("div", { class: "bar " + (e.size > 20 * 1024 ** 3 ? "tone-ember" : e.size > 5 * 1024 ** 3 ? "tone-amber" : "") }, h("i", { style: { width: (e.size / top * 100) + "%" } })), h("span", { class: "sz" }, e.size_label), h("span", { class: "pc" }, `${e.pct}%`))));
  } catch (e) { show(box, h("div", { class: "result-bad" }, "disk: " + e.message)); }
}
async function loadBig() {
  const p = $("disk-path").value.trim() || "~", box = $("big-files"); $("big-go").disabled = true;
  show(box, h("div", { class: "pad working" }, h("span", { class: "spinner" }), `walking ${p} for files ${$("big-min").selectedOptions[0].textContent} ... (can take a while on a big home)`));
  try {
    const d = await get(`/api/bigfiles?path=${encodeURIComponent(p)}&min=${encodeURIComponent($("big-min").value)}`);
    if (d.error) { show(box, h("div", { class: "pad result-bad" }, d.error)); return; }
    if (!d.files.length) { show(box, h("div", { class: "pad calm" }, icon("check"), "No file that big under " + d.path)); return; }
    show(box, h("table", { class: "table" }, h("thead", null, h("tr", null, h("th", null, "Path"), h("th", null, "Category"), h("th", null, "Modified"), h("th", { class: "num" }, "Size"), h("th", { class: "actions" }, ""))),
      h("tbody", null, d.files.map((f) => h("tr", null, h("td", { class: "path", title: f.path }, f.path.replace(/^\/Users\/[^/]+/, "~")), h("td", null, h("span", { class: "badge" }, f.category)), h("td", { class: "muted" }, new Date(f.mtime * 1000).toISOString().slice(0, 10)), h("td", { class: "num" }, f.size_label),
        h("td", { class: "actions" }, h("button", { class: "btn btn-xs btn-ghost", type: "button", disabled: !bridge.ready, onclick: async () => { try { const r = await api("reveal", f.path); if (!r.ok) toast(r.detail, "bad"); } catch (e) { toast(e.message, "bad"); } } }, "Reveal")))))),
      h("div", { class: "pad muted" }, `${d.files.length} files, ${d.total_label} total. Nothing here deletes -- use Clean, or Finder.`));
  } catch (e) { show(box, h("div", { class: "pad result-bad" }, "bigfiles: " + e.message)); }
  finally { $("big-go").disabled = false; }
}
$("disk-go").addEventListener("click", loadDisk);
$("disk-path").addEventListener("keydown", (e) => { if (e.key === "Enter") loadDisk(); });
$("big-go").addEventListener("click", loadBig);
// Both scans are explicit clicks: sizing a big home folder can take minutes.
views.disk = { enter() { /* on demand */ } };

// ── Sentinel: status, trends, detectors, opt-in toggles ─────────────────
const sn = { data: null, busy: false };
function trendRow(label, value, note, level) { return h("div", { class: "trend lv-" + level }, h("span", { class: "lb" }, label), h("span", { class: "vl" }, value), h("span", { class: "nt" }, note)); }
function heading(slope, eta, crit, unit) { if (slope > 0 && eta != null) return eta <= 0 ? `past the ${crit}${unit} critical line` : `-> ${crit}${unit} in ~${Math.max(1, Math.round(eta))} min`; return Math.abs(slope) < 1e-9 ? "stable" : "falling"; }
function renderSentinel() {
  const d = sn.data; if (!d) return;
  const st = clear($("sn-status"));
  st.appendChild(h("div", { class: "status-line" }, h("span", { class: "lamp " + (d.sampler_active ? "on" : "off") }), h("b", null, d.sampler_active ? "ACTIVE" : "STOPPED"), h("span", { class: "muted" }, d.sampler_active ? "sampling every 60 s" : "paused -- no new samples, no alerts"), h("span", { class: "grow" }),
    d.sampler_active ? h("button", { class: "btn btn-warn btn-sm", type: "button", disabled: !bridge.ready, onclick: () => sentinelToggle("pause") }, "Pause") : h("button", { class: "btn btn-primary btn-sm", type: "button", disabled: !bridge.ready, onclick: () => sentinelToggle("resume") }, "Resume")));
  st.appendChild(kvRows([["weekly health agent", d.weekly_active == null ? "macOS only" : d.weekly_active ? "ACTIVE" : "STOPPED"], ["samples on disk", d.samples], ["metrics", d.metrics_path], ["passwordless purge", d.is_mac ? (d.purge_ready ? "configured" : "not configured (macmon sentinel --setup-purge)") : "macOS only"]]));
  const tr = clear($("sn-trends")), t = d.trends || {};
  if (!t.points) { tr.appendChild(h("div", { class: "muted" }, "no history yet -- the sampler needs a few minutes")); $("sn-trend-note").textContent = ""; }
  else {
    $("sn-trend-note").textContent = `slope over ${t.points} pts / ${num(t.span_min, 0)} min`;
    const s = t.swap, r = t.ram, ld = t.load, lk = t.leaks;
    tr.appendChild(trendRow("SWAP", `${num(s.gb)} GB  ${s.slope >= 0 ? "+" : ""}${num(s.slope, 2)} GB/min`, heading(s.slope, s.eta, s.crit, " GB"), s.slope > 0.05 && s.gb >= 1 ? "medium" : "ok"));
    tr.appendChild(trendRow("RAM", `${num(r.pct, 0)}%  ${r.slope >= 0 ? "+" : ""}${num(r.slope)}%/min`, heading(r.slope, r.eta, r.crit, "%"), r.slope > 0.3 && r.pct >= 60 ? "medium" : "ok"));
    tr.appendChild(trendRow("LOAD", `${num(ld.load1)} / ${ld.ncpu} cores (${num(ld.ratio)}x)`, ld.sustained ? `${ld.sustained} sample(s) above ${num(ld.factor, 0)}x` : "nominal", ld.ratio > 1 ? "medium" : "ok"));
    const hl = t.swarm && t.swarm.headless;
    tr.appendChild(hl ? trendRow("SWARM", `headless x${hl.count} (${hl.growth >= 0 ? "+" : ""}${hl.growth})`, `<- ${t.spawn || "unknown spawner"}`, "low") : trendRow("SWARM", "none", "no runaway family", "ok"));
    tr.appendChild(trendRow("LEAKS", `${lk.leak || 0} proven / ${lk.p1 || 0} under PID 1`, (d.auto.auto_reap_orphans ? "auto-reap ON (opt-in)" : "auto-reap OFF -- notify-only") + (lk.names && lk.names.length ? "  " + lk.names.join(", ") : ""), lk.leak ? "low" : "ok"));
  }
  show($("sn-detectors"), (d.detectors || []).map((x) => h("div", { class: "det" }, h("span", { class: "nm" }, x.name), h("span", { class: "wh" }, x.what))));
  const au = clear($("sn-auto"));
  for (const [key, on] of Object.entries(d.auto || {})) {
    const sw = h("button", { class: "switch " + (on ? "is-on" : ""), type: "button", role: "switch", "aria-checked": on ? "true" : "false", disabled: !bridge.ready, onclick: () => autoToggle(key, !on, sw) });
    au.appendChild(h("div", { class: "switch-row" }, sw, h("div", { class: "sw-text" }, h("div", { class: "sw-lbl" }, d.auto_labels[key] || key), h("div", { class: "sw-key" }, key)), h("span", { class: "chip " + (on ? "chip-ember" : "chip-dim") }, on ? "ON" : "OFF")));
  }
  if (!bridge.ready) au.appendChild(h("div", { class: "ro-banner" }, icon("eye"), "Read-only tab: toggles work inside the native AegisForge window (or: macmon sentinel --enable-auto / --disable-auto)."));
  show($("sn-thresholds"), Object.entries(d.thresholds || {}).map(([k, v]) => h("div", null, h("span", null, k), h("span", null, v == null ? "--" : String(v)))));
  $("sn-conf-path").textContent = d.conf_path ? `${d.conf_path} -- read-only here` : "";
}
async function loadSentinel() {
  if (sn.busy) return; sn.busy = true;
  try { sn.data = await get("/api/sentinel"); renderSentinel(); } catch (e) { show($("sn-status"), h("div", { class: "result-bad" }, "sentinel: " + e.message)); } finally { sn.busy = false; }
}
async function sentinelToggle(verb) {
  const { ok } = await confirmModal({ title: verb === "pause" ? "Pause the sentinel?" : "Resume the sentinel?", ok: verb === "pause" ? "Pause" : "Resume", danger: verb === "pause", body: [h("p", null, verb === "pause" ? "Unloads the 60s sampler LaunchAgent. No new samples, no trend alerts, no auto-remediation until you resume." : "Reinstalls the 60s sampler LaunchAgent.")] });
  if (!ok) return;
  try { const r = await api(verb === "pause" ? "sentinel_pause" : "sentinel_resume"); toast(r.detail || "done", r.ok ? (verb === "pause" ? "warn" : "ok") : "bad"); } catch (e) { toast(e.message, "bad"); }
  setTimeout(loadSentinel, 600);
}
const AUTO_WARN = {
  auto_purge: "Level 1, non-destructive: on memory pressure the sentinel runs sudo -n purge (needs the passwordless rule from macmon sentinel --setup-purge; skipped otherwise).",
  auto_unload_ollama: "Level 1, non-destructive: idle ollama models are unloaded when RAM is critical. They reload on demand.",
  auto_trim_fleet: "Level 2: when RAM is critical the sentinel CLOSES idle AI sessions (keeps fleet_keep, resumable). Only sessions idle for idle_samples in a row.",
  auto_reap_orphans: "Level 2: SIGTERMs dev processes under PID 1 whose parent the sentinel WATCHED die, idle 5 min, with no live or listening socket. Never a process with a live parent, never one in service.",
};
async function autoToggle(key, on, sw) {
  if (on) {
    const { ok } = await confirmModal({ title: `Enable ${key}?`, ok: "Enable", ack: "I opt in: the sentinel may act on its own under these rules.", body: [h("div", { class: "warn" }, AUTO_WARN[key] || key), h("div", { class: "info" }, "Writes sentinel.conf. Turn it off any time here or with macmon sentinel --disable-auto.")] });
    if (!ok) return;
  }
  sw.classList.add("is-busy"); sw.disabled = true;
  try { const r = await api("sentinel_set_auto", key, on); if (!r.ok) throw new Error(r.detail || "refused"); toast(`${key}: ${r.on ? "ON" : "OFF"}`, r.on ? "ember" : "ok"); if (r.on && key === "auto_purge" && r.purge_ready === false) toast("auto_purge is ON but passwordless purge is not configured: run macmon sentinel --setup-purge (until then it is skipped, notifications still fire).", "warn", 9000); }
  catch (e) { toast(e.message, "bad"); }
  loadSentinel();
}
views.sentinel = { enter() { loadSentinel(); } };

// ── boot ────────────────────────────────────────────────────────────────
(async function boot() {
  await pullHistory(); await tick();
  setInterval(tick, 5000); setInterval(pullHistory, 30000);
  let start = "overview"; try { start = localStorage.getItem("af.view") || "overview"; } catch (e) { /* private mode */ }
  go(views[start] ? start : "overview");
  // once the bridge state is known, re-render the views whose buttons depend on it
  const reflow = () => { if (current === "processes") renderProcs(); if (current === "clean") renderClean(); if (current === "docker") renderDocker(); if (current === "sentinel") renderSentinel(); };
  window.addEventListener("pywebviewready", () => setTimeout(reflow, 50));
  setTimeout(reflow, 2000);
})();
