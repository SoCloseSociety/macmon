"""AegisForge.app -- the visible dashboard: a tiny local web UI.

A menu-bar LSUIElement agent has no window, so double-clicking the app shows
nothing. This serves a branded status page on 127.0.0.1 and opens it in the
browser at launch, so there IS something visible -- the fleet's Sentinel does
the same (a local HTTP origin, never file://, so nothing is cached stale).

Read-only by design: the page only shows what the 60s sampler wrote (via
``app_menubar.gather``). It performs NO system actions -- destructive levers
stay in the menu bar. Bound to 127.0.0.1 only; never 0.0.0.0.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 9137  # AegisForge's own port -- never 9101/9102 (fleet Sentinel SOC)
_HOST = "127.0.0.1"

# Brand tokens mirror aegis.AEGIS_THEME (kept literal so the page is offline).
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AegisForge</title>
<style>
:root{--bg:#070A0F;--surface:#10171F;--line:#1b2530;--text:#EEF3F8;--dim:#8393A6;
--mint:#34E5A0;--ember:#FF7A1A;--amber:#FBBF24;--sky:#7DD3FC;--crit:#FB7185;--ok:#34D399;}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 70% -10%,#0d1622,var(--bg));
color:var(--text);font:14px/1.5 ui-sans-serif,-apple-system,"IBM Plex Sans",system-ui,sans-serif;
-webkit-font-smoothing:antialiased}
.wrap{max-width:860px;margin:0 auto;padding:22px 16px 48px}
header{display:flex;align-items:center;gap:12px;padding:6px 0 18px;border-bottom:1px solid var(--line)}
.shield{width:34px;height:34px;flex:0 0 34px;border-radius:8px;
background:linear-gradient(160deg,#0e1a24,#0a1017);border:1.5px solid var(--mint);
display:grid;place-items:center;box-shadow:0 0 22px rgba(52,229,160,.18)}
.shield b{color:var(--ember);font-size:18px;line-height:1}
h1{font-size:17px;margin:0;letter-spacing:.3px}
h1 span{color:var(--dim);font-weight:400;font-size:12px;margin-left:8px}
.age{margin-left:auto;font-size:12px;color:var(--dim)}
.age.stale{color:var(--amber)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.k{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
.v{font-size:22px;font-weight:600;margin-top:2px}
.bar{height:6px;border-radius:4px;background:#0a0f16;margin-top:9px;overflow:hidden}
.bar>i{display:block;height:100%;background:var(--mint);border-radius:4px;transition:width .4s}
.sec{margin:22px 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--dim)}
.finding{background:var(--surface);border:1px solid var(--line);border-left:3px solid var(--dim);
border-radius:8px;padding:10px 12px;margin:7px 0;font-size:13px}
.finding .lvl{font-size:10px;text-transform:uppercase;letter-spacing:.7px;font-weight:700;margin-right:8px}
.sev-critical{border-left-color:var(--crit)} .sev-critical .lvl{color:var(--crit)}
.sev-high{border-left-color:var(--ember)} .sev-high .lvl{color:var(--ember)}
.sev-medium{border-left-color:var(--amber)} .sev-medium .lvl{color:var(--amber)}
.sev-low{border-left-color:var(--sky)} .sev-low .lvl{color:var(--sky)}
.sev-info .lvl{color:var(--dim)}
.calm{color:var(--mint);background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px}
.alert{color:var(--dim);font-size:12px;font-family:"IBM Plex Mono",ui-monospace,monospace;padding:3px 0}
footer{margin-top:26px;color:var(--dim);font-size:11px;text-align:center}
</style></head><body><div class="wrap">
<header><div class="shield"><b>&#9670;</b></div>
<h1>AegisForge<span>fleet health, forged</span></h1>
<div class="age" id="age">-</div></header>
<div class="grid" id="vitals"></div>
<div class="sec">Forge &mdash; anticipation</div><div id="findings"></div>
<div class="sec">Recent alerts</div><div id="alerts"></div>
<footer>engine: macmon sentinel &middot; read-only view &middot; actions live in the menu bar</footer>
</div>
<script>
const bars={cpu:["CPU","%",100],ram:["RAM","%",100],swap_gb:["Swap","GB",64],
load1:["Load","",16],disk_free_gb:["Disk free","GB",512]};
function pct(v,max){return Math.max(0,Math.min(100,(v/max)*100))}
function tint(id,v){if(id==="swap_gb")return v>16?"var(--ember)":v>6?"var(--amber)":"var(--mint)";
if(id==="ram")return v>88?"var(--ember)":v>75?"var(--amber)":"var(--mint)";
if(id==="cpu")return v>85?"var(--amber)":"var(--mint)";return "var(--mint)"}
async function tick(){
 let d;try{d=await (await fetch("/api/status",{cache:"no-store"})).json()}catch(e){return}
 const a=document.getElementById("age");
 a.textContent=d.age_label||"";a.className="age"+((d.stale)?" stale":"");
 const v=d.vitals||{},g=document.getElementById("vitals");g.innerHTML="";
 for(const id in bars){const [lbl,unit,max]=bars[id];const val=v[id];
  const shown=(val==null)?"--":(Math.round(val*10)/10);
  const w=(val==null)?0:pct(val,max);
  g.insertAdjacentHTML("beforeend",
   `<div class="card"><div class="k">${lbl}</div><div class="v">${shown}${val==null?"":unit}</div>
    <div class="bar"><i style="width:${w}%;background:${tint(id,val||0)}"></i></div></div>`);}
 const F=document.getElementById("findings");F.innerHTML="";
 if(!d.findings||!d.findings.length){F.innerHTML=`<div class="calm">All clear &mdash; forge cold.</div>`}
 else for(const f of d.findings){F.insertAdjacentHTML("beforeend",
   `<div class="finding sev-${f.severity}"><span class="lvl">${f.severity}</span>${escapeHtml(f.text)}</div>`);}
 const AL=document.getElementById("alerts");AL.innerHTML="";
 if(!d.alerts||!d.alerts.length){AL.innerHTML=`<div class="alert">none</div>`}
 else for(const x of d.alerts){AL.insertAdjacentHTML("beforeend",`<div class="alert">${escapeHtml(x)}</div>`);}
}
function escapeHtml(s){return String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
tick();setInterval(tick,5000);
</script></body></html>"""


def _age_label(age_s):
    """Human sample age + stale flag (STALE past 180s -- the sampler is 60s)."""
    if age_s is None:
        return "no sample yet", True
    age_s = int(age_s)
    txt = f"sample {age_s}s ago" if age_s < 90 else f"sample {age_s // 60}m ago"
    stale = age_s > 180
    return (txt + " / STALE" if stale else txt), stale


def status_dict() -> dict:
    """gather() shaped for JSON: findings as {severity,text}, plus a sample age."""
    from . import app_menubar as mb
    snap = mb.gather()
    findings = [{"severity": mb._finding_severity(f), "text": mb._finding_text(f)}
                for f in snap.get("findings", [])]
    label, stale = _age_label(snap.get("age_s"))
    return {
        "vitals": snap.get("vitals", {}),
        "worst": snap.get("worst", "ok"),
        "findings": findings,
        "alerts": snap.get("alerts", []),
        "age_label": label,
        "stale": stale,
        "error": snap.get("error"),
    }


class _Handler(BaseHTTPRequestHandler):
    def _send(self, code, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/" or path == "/index.html":
            self._send(200, _PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif path == "/api/status":
            try:
                body = json.dumps(status_dict()).encode("utf-8")
            except Exception as e:
                body = json.dumps({"error": str(e), "vitals": {}, "findings": [],
                                   "alerts": [], "worst": "ok"}).encode("utf-8")
            self._send(200, body, "application/json")
        else:
            self._send(404, b"not found", "text/plain")

    def log_message(self, *a):
        pass  # keep the console quiet


def _bind(port: int) -> ThreadingHTTPServer:
    """Bind 127.0.0.1:port, falling back to a free ephemeral port if taken."""
    try:
        return ThreadingHTTPServer((_HOST, port), _Handler)
    except OSError:
        return ThreadingHTTPServer((_HOST, 0), _Handler)  # 0 -> OS picks a free port


def serve(port: int = DEFAULT_PORT) -> tuple[ThreadingHTTPServer, int]:
    """Start the dashboard server in a daemon thread. Returns (server, port)."""
    srv = _bind(port)
    real_port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, real_port


def url_for(port: int) -> str:
    return f"http://{_HOST}:{port}/"


def open_in_browser(port: int) -> None:
    """Open the dashboard in the default browser. `open` on macOS (reliable from
    a bundled app, per the fleet's Sentinel), webbrowser elsewhere."""
    u = url_for(port)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", u])
        else:
            import webbrowser
            webbrowser.open(u)
    except Exception:
        pass


def _free_port_note() -> int:
    """(used only by tests) a definitely-free port so a fallback can be forced."""
    s = socket.socket()
    s.bind((_HOST, 0))
    p = s.getsockname()[1]
    s.close()
    return p
