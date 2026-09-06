"""
build_interactive_demo.py
=========================
Builds sentinel_stakeholder_demo.html — the full interactive control-panel demo.
Run from repo root: python demo/build_interactive_demo.py
"""
import json
import pathlib

ROOT = pathlib.Path(__file__).parent.parent
SD   = ROOT / "demo" / "sample_data"
OUT  = ROOT / "demo" / "sentinel_stakeholder_demo.html"


def _j(fname):
    return json.loads((SD / fname).read_text(encoding="utf-8"))


# ── Load all watchlist data ──────────────────────────────────────────────────
INDEX = _j("watchlist_index.json")
WATCHLIST_DATA = {}
for asset in INDEX["assets"]:
    for cond in asset["conditions"]:
        key = f"{asset['id']}_{cond['id']}"
        WATCHLIST_DATA[key] = _j(cond["file"])

# ── Load legacy static detector results (for Engineering section) ────────────
legacy = {}
for det in ["spoofing", "circular_trading", "coordinated_pump",
            "oi_manipulation", "basis_distortion", "option_pinning"]:
    legacy[det] = {
        "trigger": _j(f"{det}_trigger.json"),
        "normal":  _j(f"{det}_normal.json"),
    }

DATA_JS = f"""
const WATCHLIST_INDEX = {json.dumps(INDEX, indent=2)};
const WATCHLIST_DATA = {json.dumps(WATCHLIST_DATA, indent=2, default=str)};
"""

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sentinel — Market Surveillance Platform</title>
<meta name="description" content="Sentinel: interactive market surveillance control panel. 6 detection engines running against realistic sample data.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<style>
/* ── Reset & tokens ──────────────────────────────────────────────────────── */
:root{
  --bg:#080f1a;--sf:#0d1726;--sf2:#111e2e;--sf3:#162032;
  --b:#1a2d42;--b2:#213552;--b3:#253d5e;
  --re:#22d3ee;--rd:#0e7490;--rb:rgba(34,211,238,.07);
  --sy:#f59e0b;--sd:#92400e;--sb:rgba(245,158,11,.08);
  --cr:#ef4444;--ch:#f97316;--cm:#eab308;--cl:#6b7280;
  --t1:#e2e8f0;--t2:#94a3b8;--t3:#4a6180;--t4:#2d4a66;
  --fm:'Cascadia Code','Fira Code',Consolas,monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--t1);font-family:'Inter',system-ui,sans-serif;
  font-size:14px;line-height:1.6}

/* ── Nav ─────────────────────────────────────────────────────────────────── */
nav{position:sticky;top:0;z-index:200;background:rgba(8,15,26,.97);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--b);
  padding:10px 28px;display:flex;align-items:center;
  justify-content:space-between;flex-wrap:wrap;gap:8px}
.nbrand{font-family:var(--fm);font-size:12px;font-weight:700;
  letter-spacing:.06em;color:var(--re)}
.nlinks{display:flex;gap:18px;list-style:none;flex-wrap:wrap}
.nlinks a{font-size:11px;color:var(--t3);text-decoration:none;
  letter-spacing:.04em;text-transform:uppercase;transition:color .15s}
.nlinks a:hover{color:var(--t1)}

/* ── Layout helpers ─────────────────────────────────────────────────────── */
.sec{padding:56px 0;border-bottom:1px solid var(--b)}
.sec:last-of-type{border-bottom:none}
.c{max-width:1180px;margin:0 auto;padding:0 28px}
.slbl{font-family:var(--fm);font-size:10px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--t3);margin-bottom:8px}
h1{font-size:clamp(24px,3.5vw,38px);font-weight:600;line-height:1.2;margin-bottom:12px}
h2{font-size:clamp(18px,2.5vw,24px);font-weight:600;margin-bottom:10px}
.lede{font-size:14px;color:var(--t2);max-width:660px;line-height:1.7;margin-bottom:24px}

/* ── Badges ──────────────────────────────────────────────────────────────── */
.bdg{display:inline-flex;align-items:center;gap:4px;padding:2px 8px;
  font-family:var(--fm);font-size:10px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;border-radius:2px}
.bdg::before{content:'';width:5px;height:5px;border-radius:50%;background:currentColor;flex-shrink:0}
.br{background:var(--rb);color:var(--re);border:1px solid var(--rd)}
.bs{background:var(--sb);color:var(--sy);border:1px solid var(--sd)}
.sev{font-family:var(--fm);font-size:10px;font-weight:700;letter-spacing:.08em;
  padding:2px 8px;color:#000;text-transform:uppercase}
.sev-critical{background:var(--cr)}.sev-high{background:var(--ch)}
.sev-medium{background:var(--cm)}.sev-low{background:var(--cl);color:#fff}

/* ═══════════════════════════════════════════════════════════════════════════
   CONTROL PANEL
   ═══════════════════════════════════════════════════════════════════════════ */
#cp{background:var(--bg)}
.cp-inline-note{font-size:12px;color:var(--t3);border-left:2px solid var(--b3);
  padding:8px 14px;margin-bottom:20px;line-height:1.65;max-width:760px}
.cp-inline-note strong{color:var(--t2)}

/* Three-column layout */
.cp-grid{display:grid;grid-template-columns:220px 1fr 340px;
  gap:1px;background:var(--b);min-height:560px}
@media(max-width:900px){
  .cp-grid{grid-template-columns:1fr;min-height:auto}
}

/* ── Watchlist panel (left) ──────────────────────────────────────────────── */
.wl-panel{background:var(--sf);display:flex;flex-direction:column}
.wl-hdr{padding:12px 14px;border-bottom:1px solid var(--b);
  font-family:var(--fm);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--t3)}
.wl-asset{padding:12px 14px;border-bottom:1px solid var(--b);cursor:pointer;
  transition:background .12s;position:relative;user-select:none}
.wl-asset:hover{background:var(--sf2)}
.wl-asset.selected{background:var(--sf3);border-left:2px solid var(--re)}
.wl-asset:not(.selected){border-left:2px solid transparent}
.wl-row{display:flex;align-items:center;gap:8px}
.wl-dot{width:8px;height:8px;border-radius:50%;background:var(--b3);
  flex-shrink:0;transition:background .3s,box-shadow .3s}
.wl-dot.clean{background:#22c55e;box-shadow:0 0 6px rgba(34,197,94,.5)}
.wl-dot.sev-critical{background:var(--cr);box-shadow:0 0 6px rgba(239,68,68,.6);animation:pulse .8s infinite alternate}
.wl-dot.sev-high{background:var(--ch);box-shadow:0 0 6px rgba(249,115,22,.5)}
.wl-dot.sev-medium{background:var(--cm);box-shadow:0 0 5px rgba(234,179,8,.4)}
.wl-dot.sev-low{background:var(--cl);box-shadow:none}
@keyframes pulse{from{opacity:.6}to{opacity:1}}
.wl-name{font-family:var(--fm);font-size:12px;font-weight:700;color:var(--t1)}
.wl-type{font-size:10px;color:var(--t3);font-family:var(--fm)}
.wl-summary{font-size:10px;color:var(--sy);margin-top:3px;font-family:var(--fm);
  line-height:1.4;word-break:break-word}
.wl-clean-lbl{font-size:10px;color:var(--re);margin-top:3px;font-family:var(--fm)}
.wl-legend{padding:12px 14px;margin-top:auto;border-top:1px solid var(--b);font-size:10px;color:var(--t4);line-height:1.6}
.wl-legend span{display:flex;align-items:center;gap:6px;margin-bottom:4px}
.leg-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}

/* ── Control panel (center) ───────────────────────────────────────────────── */
.ctrl-panel{background:var(--sf2);display:flex;flex-direction:column}
.ctrl-hdr{padding:14px 20px;border-bottom:1px solid var(--b);
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.ctrl-asset-name{font-size:16px;font-weight:600;color:var(--t1)}
.ctrl-asset-type{font-family:var(--fm);font-size:10px;color:var(--t3);
  letter-spacing:.08em;text-transform:uppercase;margin-top:2px}
.ctrl-body{padding:20px;flex:1;display:flex;flex-direction:column;gap:16px}
.ctrl-placeholder{flex:1;display:flex;align-items:center;justify-content:center;
  color:var(--t4);font-family:var(--fm);font-size:12px;text-align:center;
  padding:40px;border:1px dashed var(--b2);margin:20px}

/* Condition picker */
.cond-label{font-family:var(--fm);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--t3);margin-bottom:8px}
.cond-btns{display:flex;flex-wrap:wrap;gap:6px}
.cond-btn{font-family:var(--fm);font-size:10px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;padding:6px 14px;border:1px solid var(--b2);
  background:none;color:var(--t2);cursor:pointer;transition:all .12s}
.cond-btn:hover{background:var(--sf3);border-color:var(--b3);color:var(--t1)}
.cond-btn.active{background:var(--sb);color:var(--sy);border-color:var(--sd)}
.cond-desc{font-size:12px;color:var(--t2);line-height:1.6;padding:8px 12px;
  border-left:2px solid var(--b3);background:rgba(0,0,0,.1)}

/* Run button */
.run-btn{display:flex;align-items:center;gap:10px;padding:11px 22px;
  background:var(--re);color:#000;font-family:var(--fm);font-size:11px;
  font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  border:none;cursor:pointer;transition:background .15s,opacity .15s;align-self:flex-start}
.run-btn:hover{background:#38bdf8}
.run-btn:disabled{background:var(--b2);color:var(--t3);cursor:not-allowed}
.run-btn .spinner{width:14px;height:14px;border:2px solid rgba(0,0,0,.3);
  border-top-color:#000;border-radius:50%;animation:spin .6s linear infinite;display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.run-btn.running .spinner{display:block}
.run-btn.running .btn-text{opacity:.6}

/* Input visualization */
.viz-section{border:1px solid var(--b2);background:var(--bg)}
.viz-hdr{padding:8px 14px;border-bottom:1px solid var(--b2);
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px}
.viz-title{font-family:var(--fm);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--t3)}
.viz-anim{padding:12px 14px;max-height:180px;overflow-y:auto}

/* Scrollable data tape */
.data-tape{font-family:var(--fm);font-size:11px;color:var(--t2);line-height:1.8}
.tape-row{display:flex;gap:12px;padding:1px 0;border-bottom:1px solid rgba(255,255,255,.03)}
.tape-row:last-child{border-bottom:none}
.tape-col{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tape-hdr{color:var(--t3);font-size:10px;font-weight:600;border-bottom:1px solid var(--b2)!important;padding-bottom:4px;margin-bottom:2px}
.tape-cancelled{color:var(--cr);opacity:.85}
.tape-executed{color:#22c55e}
.tape-fired{color:var(--sy)}

/* Scanning animation */
.scan-progress{padding:12px 14px}
.scan-step{display:flex;align-items:center;gap:10px;padding:5px 0;font-family:var(--fm);font-size:11px;color:var(--t3)}
.scan-step.done{color:var(--re)}
.scan-step.active{color:var(--t1)}
.scan-ico{width:14px;height:14px;flex-shrink:0;display:flex;align-items:center;justify-content:center;font-size:11px}

/* ── Result panel (right) ─────────────────────────────────────────────────── */
.res-panel{background:var(--sf);border-left:1px solid var(--b);
  display:flex;flex-direction:column}
.res-hdr{padding:12px 16px;border-bottom:1px solid var(--b);
  font-family:var(--fm);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--t3)}
.res-body{padding:16px;flex:1;display:flex;flex-direction:column;gap:12px;
  overflow-y:auto;max-height:600px}
.res-placeholder{flex:1;display:flex;align-items:center;justify-content:center;
  color:var(--t4);font-family:var(--fm);font-size:11px;text-align:center;padding:20px}
/* Alert card */
.alert-card{border:1px solid var(--b3);background:var(--sf2)}
.alert-card-hdr{padding:10px 14px;border-bottom:1px solid var(--b3);
  display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.alert-score{font-family:var(--fm);font-size:18px;font-weight:700;color:var(--t1)}
.alert-det{font-family:var(--fm);font-size:10px;color:var(--t3);margin-top:1px}
.alert-expl{padding:12px 14px;font-family:var(--fm);font-size:11px;
  color:var(--t2);line-height:1.7;white-space:pre-wrap;word-break:break-word;
  max-height:200px;overflow-y:auto;border-bottom:1px solid var(--b2)}
/* Evidence table */
.ev-label{font-family:var(--fm);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--t3);padding:8px 14px 4px}
.ev-wrap{overflow-x:auto}
.ev-table{width:100%;border-collapse:collapse;font-family:var(--fm);font-size:10px}
.ev-table th{text-align:left;color:var(--t3);padding:4px 10px;
  border-bottom:1px solid var(--b2);white-space:nowrap}
.ev-table td{padding:4px 10px;border-bottom:1px solid rgba(255,255,255,.04);
  color:var(--t2);white-space:nowrap}
.ev-table tr:last-child td{border-bottom:none}
.ev-table td.flag{color:var(--sy);font-weight:700}
/* Clean card */
.clean-card{border:1px solid var(--rd);background:var(--rb);padding:14px}
.clean-icon{font-size:24px;margin-bottom:8px}
.clean-title{font-family:var(--fm);font-size:12px;color:var(--re);
  font-weight:700;letter-spacing:.06em;margin-bottom:8px}
.clean-det-row{padding:5px 0;border-bottom:1px solid rgba(34,211,238,.1);
  font-family:var(--fm);font-size:10px;color:var(--t3)}
.clean-det-row:last-child{border-bottom:none}
.clean-det-name{color:var(--t2);font-weight:600;margin-bottom:2px}
.caveat-note{font-size:11px;color:var(--t3);font-style:italic;line-height:1.6;
  margin-top:8px;border-top:1px solid rgba(34,211,238,.1);padding-top:8px}

/* ═══════════════════════════════════════════════════════════════════════════
   OTHER SECTIONS (negative control, engineering, status, arch)
   ═══════════════════════════════════════════════════════════════════════════ */
#nc{background:var(--sf2)}
.fbig{display:flex;gap:40px;align-items:flex-end;margin-bottom:24px;flex-wrap:wrap}
.fnum{font-family:var(--fm);font-size:clamp(44px,7vw,72px);font-weight:700;
  color:var(--re);line-height:1}
.fden{font-family:var(--fm);font-size:20px;color:var(--t3);padding-bottom:6px}
.frate{font-family:var(--fm);font-size:28px;color:var(--re)}
.fsub{font-family:var(--fm);font-size:11px;color:var(--t3)}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-family:var(--fm);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--t3);padding:7px 11px;
  border-bottom:1px solid var(--b)}
td{font-family:var(--fm);font-size:11px;padding:7px 11px;
  border-bottom:1px solid var(--b);color:var(--t2)}
tr:last-child td{border-bottom:none}
.sym{color:var(--t1);font-weight:700}.zero{color:var(--re)}
.fwrap{border:1px solid var(--rd);margin-top:18px}
.fthdr{background:var(--rb);padding:8px 14px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;
  gap:6px;border-bottom:1px solid var(--rd)}
.ftitle{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--re)}
.fint{max-width:560px;font-size:13px;color:var(--t2);line-height:1.7;
  padding:14px 18px;border-left:2px solid var(--rd);background:var(--rb);margin-top:18px}

#eng{background:var(--bg)}
.egrid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--b);margin-bottom:24px}
@media(max-width:800px){.egrid{grid-template-columns:repeat(2,1fr)}}
.ecell{background:var(--sf);padding:20px 16px}
.eval{font-family:var(--fm);font-size:24px;font-weight:700;color:var(--t1);line-height:1;margin-bottom:3px}
.eunit{font-family:var(--fm);font-size:11px;color:var(--t2)}
.elbl{font-size:11px;color:var(--t3);margin-top:3px}
.esrc{font-size:10px;color:var(--t4);font-style:italic;margin-top:2px}
.rnote{display:flex;gap:12px;align-items:flex-start;padding:12px 16px;border:1px solid var(--b2);background:var(--sf)}
.rico{font-size:16px;flex-shrink:0;margin-top:1px}
.rtxt{font-size:12px;color:var(--t2);line-height:1.6}
.rtxt strong{color:var(--t1)}

#hs{background:var(--sf2)}
.sgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--b)}
@media(max-width:800px){.sgrid{grid-template-columns:1fr}}
.scol{background:var(--bg);padding:24px 20px}
.shdr{display:flex;align-items:center;gap:9px;margin-bottom:16px;padding-bottom:10px;border-bottom:1px solid var(--b)}
.sico{width:24px;height:24px;border-radius:2px;display:flex;align-items:center;justify-content:center;font-size:11px;flex-shrink:0}
.sip{background:rgba(34,211,238,.12);color:var(--re)}
.sii{background:rgba(245,158,11,.12);color:var(--sy)}
.sib{background:rgba(100,116,139,.12);color:var(--t3)}
.stp{font-family:var(--fm);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--re)}
.sti{font-family:var(--fm);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--sy)}
.stb{font-family:var(--fm);font-size:10px;letter-spacing:.08em;text-transform:uppercase;color:var(--t3)}
.sit{font-size:12px;color:var(--t2);padding:7px 0;border-bottom:1px solid var(--b);line-height:1.5}
.sit:last-child{border-bottom:none}
.sit strong{display:block;font-size:12px;margin-bottom:1px;color:var(--t1)}
.snote{margin-top:12px;font-size:11px;color:var(--t3);font-style:italic;line-height:1.6}

#arch{background:var(--bg)}
.awrap{width:100%;overflow-x:auto;border:1px solid var(--b);background:var(--sf);padding:24px}
footer{border-top:1px solid var(--b);padding:20px 28px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px}
.fbrand{font-family:var(--fm);font-size:11px;color:var(--t3)}
.fnote{font-size:11px;color:var(--t3);max-width:500px;line-height:1.5}
</style>
</head>
<body>

<nav>
  <span class="nbrand">SENTINEL // MARKET SURVEILLANCE</span>
  <ul class="nlinks">
    <li><a href="#cp">Control Panel</a></li>
    <li><a href="#nc">False Positives</a></li>
    <li><a href="#eng">Engineering</a></li>
    <li><a href="#hs">Status</a></li>
    <li><a href="#arch">Architecture</a></li>
  </ul>
</nav>

<!-- =========================================================
  SECTION 1: INTERACTIVE CONTROL PANEL
  ========================================================= -->
<section id="cp" class="sec">
<div class="c">
  <p class="slbl">Interactive surveillance — 5 assets · 12 conditions</p>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap">
    <h2>Analyst Control Panel</h2>
    <span class="bdg bs">Sample data · real detector output</span>
  </div>

  <div class="cp-inline-note">
    <strong>How this works:</strong> Select an asset, pick a market condition, and click
    <em>Run Surveillance Scan</em>. The panel loads the actual output of calling the
    corresponding <code>app/detection/*.py</code> function against the sample data you
    selected — computed in advance by <code>demo/generate_watchlist_samples.py</code>.
    It is not connected to live market data and it is not a JavaScript simulation of
    the detection logic.
    <a href="#hs" style="color:var(--re);text-decoration:none">See Honest Status ↓</a>
    for what live data would require.
  </div>

  <div class="cp-grid">

    <!-- ── LEFT: Watchlist ────────────────────────────────────────────── -->
    <div class="wl-panel" id="wl-panel">
      <div class="wl-hdr">Watchlist — 5 assets</div>
      <!-- assets injected by JS -->
      <div class="wl-legend">
        <span><span class="leg-dot" style="background:#374151"></span>Not yet scanned</span>
        <span><span class="leg-dot" style="background:#22c55e"></span>Scan complete — clean</span>
        <span><span class="leg-dot" style="background:#f97316"></span>Alert: high / medium</span>
        <span><span class="leg-dot" style="background:#ef4444"></span>Alert: critical</span>
        <span><span class="leg-dot" style="background:#6b7280"></span>Alert: low</span>
      </div>
    </div>

    <!-- ── CENTER: Control panel ─────────────────────────────────────── -->
    <div class="ctrl-panel" id="ctrl-panel">
      <div class="ctrl-hdr">
        <div>
          <div class="ctrl-asset-name" id="ctrl-asset-name">Select an asset</div>
          <div class="ctrl-asset-type" id="ctrl-asset-type">Click any row in the watchlist →</div>
        </div>
      </div>
      <div class="ctrl-body" id="ctrl-body">
        <div class="ctrl-placeholder" id="ctrl-placeholder">
          Select an asset from the watchlist<br>to configure and run a scan.
        </div>
      </div>
    </div>

    <!-- ── RIGHT: Result panel ───────────────────────────────────────── -->
    <div class="res-panel" id="res-panel">
      <div class="res-hdr">Scan Result</div>
      <div class="res-body" id="res-body">
        <div class="res-placeholder" id="res-placeholder">
          Run a scan to see detector output here.
        </div>
      </div>
    </div>

  </div><!-- .cp-grid -->
</div>
</section>

<!-- =========================================================
  SECTION 2: NEGATIVE CONTROL — REAL DATA
  ========================================================= -->
<section id="nc" class="sec">
<div class="c">
  <p class="slbl">Real-data validation</p>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <h2>False Positive Rate on Real NSE Data</h2>
    <span class="bdg br">Verified real data</span>
  </div>
  <p class="lede">
    Run against 90 real NSE trading days across five of India's most liquid large-caps.
    No spurious alerts. This is verifiable from <code>backtest/results/negative_controls.json</code>.
    It does not prove the system catches real manipulation — see Status below.
  </p>
  <div class="fbig">
    <div><div class="fnum">0</div><div class="fsub" style="margin-top:4px">days flagged</div></div>
    <div class="fden">/ 90 trading days</div>
    <div><div class="frate">0.0%</div><div class="fsub">false positive rate</div></div>
  </div>
  <div class="fwrap">
    <div class="fthdr">
      <span class="ftitle">Per-symbol — backtest/results/negative_controls.json</span>
      <span class="bdg br">Verified real data</span>
    </div>
    <table>
      <thead><tr><th>Symbol</th><th>Exchange</th><th>Days tested</th><th>Days flagged</th><th>FP rate</th></tr></thead>
      <tbody>
        <tr><td class="sym">RELIANCE</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr><td class="sym">TCS</td>     <td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr><td class="sym">HDFCBANK</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr><td class="sym">INFY</td>    <td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr><td class="sym">ICICIBANK</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr style="background:var(--rb)">
          <td class="zero" style="font-weight:700">TOTAL</td><td>NSE</td>
          <td class="zero" style="font-weight:700">90</td>
          <td class="zero" style="font-weight:700">0</td>
          <td class="zero" style="font-weight:700">0.0%</td>
        </tr>
      </tbody>
    </table>
  </div>
  <div class="fint">
    A 0.0% false positive rate on large-cap stocks shows the detector does not flag normal
    institutional and retail flow in India's most liquid equities. It does not prove the
    system catches actual manipulation — that validation requires account-level order data
    not available in public archives.
  </div>
</div>
</section>

<!-- =========================================================
  SECTION 3: ENGINEERING
  ========================================================= -->
<section id="eng" class="sec">
<div class="c">
  <p class="slbl">Engineering validation</p>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <h2>Performance, Correctness &amp; Security</h2>
    <span class="bdg br">Verified real data</span>
  </div>
  <p class="lede">Every figure was measured by running the actual system.</p>
  <div class="egrid">
    <div class="ecell">
      <div class="eval">19,224</div><div class="eunit">orders / sec</div>
      <div class="elbl">Spoofing detector throughput</div>
      <div class="esrc">stress/test_ingestion_volume.py · 100k orders</div>
    </div>
    <div class="ecell">
      <div class="eval">51,349</div><div class="eunit">orders / sec</div>
      <div class="elbl">Coordinated pump throughput</div>
      <div class="esrc">stress/test_ingestion_volume.py · 100k orders</div>
    </div>
    <div class="ecell">
      <div class="eval">273</div><div class="eunit">tests passing</div>
      <div class="elbl">0 failures · full suite</div>
      <div class="esrc">pytest tests/</div>
    </div>
    <div class="ecell">
      <div class="eval">500</div><div class="eunit">concurrent writes</div>
      <div class="elbl">Exactly 1 alert per race · 10 trials × 50 threads</div>
      <div class="esrc">stress/test_concurrent_access.py</div>
    </div>
  </div>
  <div class="rnote">
    <div class="rico">&#128274;</div>
    <div class="rtxt">
      <strong>Race condition found and fixed during real stress testing.</strong>
      Concurrent detector threads produced duplicate Alert rows.
      Fix: <code>UniqueConstraint("instrument_id","pattern_type","window_start")</code>.
      Verified: 500 concurrent writes, 10 trials × 50 threads — exactly 1 alert survived every time.
    </div>
  </div>
</div>
</section>

<!-- =========================================================
  SECTION 4: HONEST STATUS
  ========================================================= -->
<section id="hs" class="sec">
<div class="c">
  <p class="slbl">Validation status — September 2026</p>
  <h2>What Is and Is Not Proven</h2>
  <p class="lede">Every claim in this demo is in the PROVEN column.</p>
  <div class="sgrid">
    <div class="scol">
      <div class="shdr"><div class="sico sip">✓</div><span class="stp">Proven</span></div>
      <div class="sit"><strong>Engineering correctness</strong>273 tests pass across all 6 detectors, DB layer, ingestion, security.</div>
      <div class="sit"><strong>Performance at volume</strong>Spoofing 19,224/sec; pump 51,349/sec — well within the 120s/100k threshold.</div>
      <div class="sit"><strong>0.0% FP on large-cap NSE data</strong>90 real days, 5 NSE stocks, 0 spurious alerts. Verifiable from negative_controls.json.</div>
      <div class="sit"><strong>All 6 detectors produce consistent results on sample data</strong>Each fires on the triggering scenario and does not fire on the normal scenario. Real detector output, not simulated.</div>
      <div class="sit"><strong>Concurrency integrity</strong>Race condition found, fixed, and verified under 500 concurrent writes.</div>
      <div class="sit"><strong>Interactive demo integrity</strong>Every selectable condition in the control panel has a real precomputed result file. The UI cannot display a combination without one — it reads from the same index that built the JSON files.</div>
    </div>
    <div class="scol">
      <div class="shdr"><div class="sico sii">○</div><span class="sti">In Progress</span></div>
      <div class="sit"><strong>Detection efficacy against real confirmed manipulation — all 6 detectors</strong>None of the 6 detectors have been validated against a confirmed real historical manipulation case. The control panel scenarios demonstrate the logic fires on the expected pattern; they do not constitute detection of actual manipulation.</div>
      <div class="sit"><strong>Low-scoring detectors: OI manipulation and basis distortion</strong>Sample data triggers these at "low" severity (scores 0.185 and 0.256). Both thresholds are documented as UNVALIDATED GUESSes needing calibration from historical distributions.</div>
      <div class="sit"><strong>Real-case SEBI backtest structurally untestable</strong>Three real SEBI enforcement orders identified. All three involve BSE-listed scrips absent from NSE bhavcopy — cannot test without BSE data access.</div>
    </div>
    <div class="scol">
      <div class="shdr"><div class="sico sib">✗</div><span class="stb">Not Possible Without Official Data</span></div>
      <div class="sit"><strong>Spoofing and circular trading efficacy</strong>Require order-lifecycle data with per-order counterparty IDs. NSE/BSE have never published historical order books publicly.</div>
      <div class="sit"><strong>Coordinated pump with real dormancy data</strong>Requires historical account-level trade records. Only available to SEBI/NSE/BSE surveillance teams under formal access.</div>
      <div class="sit"><strong>OI and option pinning validation</strong>Historical option chain OI snapshots do not exist in any public Indian archive. NSE publishes only the current live chain.</div>
      <div class="snote">This is a structural fact about Indian market data infrastructure. Every commercial surveillance vendor faces the same constraint. Efficacy validation requires formal data-sharing agreements with exchanges.</div>
    </div>
  </div>
</div>
</section>

<!-- =========================================================
  SECTION 5: ARCHITECTURE
  ========================================================= -->
<section id="arch" class="sec">
<div class="c">
  <p class="slbl">System design</p>
  <h2>Architecture — Real Module Names</h2>
  <p class="lede">All labels are actual filenames in this repository.</p>
  <div class="awrap">
    <svg viewBox="0 0 960 370" xmlns="http://www.w3.org/2000/svg"
         font-family="Consolas,'Courier New',monospace" font-size="11"
         style="width:100%;max-width:960px;display:block;margin:0 auto">
      <defs>
        <marker id="a1" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#253d5e"/></marker>
        <marker id="a2" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#0e7490"/></marker>
      </defs>
      <text x="4" y="38" fill="#4a6180" font-size="9" letter-spacing="1">DATA SOURCES</text>
      <text x="4" y="140" fill="#4a6180" font-size="9" letter-spacing="1">INGESTION</text>
      <text x="4" y="230" fill="#4a6180" font-size="9" letter-spacing="1">DETECTION</text>
      <text x="4" y="320" fill="#4a6180" font-size="9" letter-spacing="1">OUTPUT</text>
      <line x1="100" y1="50" x2="950" y2="50" stroke="#1e3048" stroke-width=".5"/>
      <line x1="100" y1="148" x2="950" y2="148" stroke="#1e3048" stroke-width=".5"/>
      <line x1="100" y1="242" x2="950" y2="242" stroke="#1e3048" stroke-width=".5"/>
      <rect x="108" y="14" width="155" height="32" fill="#111e2e" stroke="#1e3048"/>
      <text x="186" y="28" text-anchor="middle" fill="#94a3b8">NSE Bhavcopy</text>
      <text x="186" y="40" text-anchor="middle" fill="#4a6180" font-size="9">archives.nseindia.com</text>
      <rect x="272" y="14" width="158" height="32" fill="#111e2e" stroke="#1e3048"/>
      <text x="351" y="28" text-anchor="middle" fill="#94a3b8">NSE Option Chain</text>
      <text x="351" y="40" text-anchor="middle" fill="#4a6180" font-size="9">OI + Greeks</text>
      <rect x="438" y="14" width="155" height="32" fill="#111e2e" stroke="#1e3048"/>
      <text x="516" y="28" text-anchor="middle" fill="#94a3b8">NSE Bulk Deals</text>
      <text x="516" y="40" text-anchor="middle" fill="#4a6180" font-size="9">block trades</text>
      <rect x="612" y="6" width="200" height="40" fill="#0a1929" stroke="#253d5e"/>
      <text x="712" y="20" text-anchor="middle" fill="#22d3ee">resilience.py</text>
      <text x="712" y="32" text-anchor="middle" fill="#4a6180" font-size="9">retry_with_backoff · CircuitBreaker</text>
      <text x="712" y="44" text-anchor="middle" fill="#4a6180" font-size="9">bhavcopy_circuit · delivery_circuit</text>
      <rect x="108" y="115" width="155" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="186" y="129" text-anchor="middle" fill="#e2e8f0">nse_bhavcopy.py</text>
      <text x="186" y="141" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
      <rect x="272" y="115" width="158" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="351" y="129" text-anchor="middle" fill="#e2e8f0">nse_option_chain.py</text>
      <text x="351" y="141" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
      <rect x="438" y="115" width="155" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="516" y="129" text-anchor="middle" fill="#e2e8f0">nse_bulk_deals.py</text>
      <text x="516" y="141" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
      <line x1="186" y1="46" x2="186" y2="113" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <line x1="351" y1="46" x2="351" y2="113" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <line x1="516" y1="46" x2="516" y2="113" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <rect x="108" y="158" width="135" height="28" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="176" y="170" text-anchor="middle" fill="#e2e8f0">spoofing.py</text>
      <text x="176" y="182" text-anchor="middle" fill="#4a6180" font-size="9">cancel·size·impact</text>
      <rect x="250" y="158" width="142" height="28" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="321" y="170" text-anchor="middle" fill="#e2e8f0">circular_trading.py</text>
      <text x="321" y="182" text-anchor="middle" fill="#4a6180" font-size="9">graph cycles</text>
      <rect x="398" y="158" width="150" height="28" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="473" y="170" text-anchor="middle" fill="#e2e8f0">coordinated_pump.py</text>
      <text x="473" y="182" text-anchor="middle" fill="#4a6180" font-size="9">dormancy·vol spike</text>
      <rect x="108" y="200" width="135" height="28" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="176" y="212" text-anchor="middle" fill="#e2e8f0">oi_manipulation.py</text>
      <text x="176" y="224" text-anchor="middle" fill="#4a6180" font-size="9">OI concentration</text>
      <rect x="250" y="200" width="142" height="28" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="321" y="212" text-anchor="middle" fill="#e2e8f0">basis_distortion.py</text>
      <text x="321" y="224" text-anchor="middle" fill="#4a6180" font-size="9">futures-spot spread</text>
      <rect x="398" y="200" width="150" height="28" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="473" y="212" text-anchor="middle" fill="#e2e8f0">option_pinning.py</text>
      <text x="473" y="224" text-anchor="middle" fill="#4a6180" font-size="9">strike OI · DTE</text>
      <line x1="290" y1="145" x2="290" y2="156" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <rect x="108" y="254" width="136" height="30" fill="#0a1929" stroke="#0e7490"/>
      <text x="176" y="268" text-anchor="middle" fill="#22d3ee">evidence.py</text>
      <text x="176" y="280" text-anchor="middle" fill="#0e7490" font-size="9">evidence log · alert builder</text>
      <rect x="252" y="254" width="140" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="322" y="268" text-anchor="middle" fill="#e2e8f0">app/db/models.py</text>
      <text x="322" y="280" text-anchor="middle" fill="#4a6180" font-size="9">Alert · UniqueConstraint</text>
      <rect x="400" y="254" width="104" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="452" y="268" text-anchor="middle" fill="#e2e8f0">access_log.py</text>
      <text x="452" y="280" text-anchor="middle" fill="#4a6180" font-size="9">audit trail</text>
      <rect x="512" y="254" width="74" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="549" y="268" text-anchor="middle" fill="#e2e8f0">pii.py</text>
      <text x="549" y="280" text-anchor="middle" fill="#4a6180" font-size="9">masking</text>
      <rect x="594" y="254" width="92" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="640" y="268" text-anchor="middle" fill="#e2e8f0">retention.py</text>
      <text x="640" y="280" text-anchor="middle" fill="#4a6180" font-size="9">lifecycle</text>
      <line x1="290" y1="228" x2="290" y2="252" stroke="#0e7490" stroke-width="1" marker-end="url(#a2)"/>
    </svg>
  </div>
</div>
</section>

<footer>
  <div class="fbrand">SENTINEL // v1.0-alpha // September 2026</div>
  <div class="fnote">
    Control panel results are pre-computed outputs of calling
    app/detection/*.py functions. Source: demo/sample_data/watchlist_*.json.
    All sample data is synthetic — not derived from any real session or account.
  </div>
</footer>

<script>
"use strict";
""" + DATA_JS + """

// ── Helpers ───────────────────────────────────────────────────────────────

function esc(s) {
  return String(s || "")
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function sevClass(sev) { return sev ? "sev sev-"+sev.toLowerCase() : ""; }
function dotClass(sev) { return sev ? "wl-dot sev-"+sev.toLowerCase() : "wl-dot"; }

// ── Panel state ────────────────────────────────────────────────────────────
var selectedAsset = null;
var selectedCond  = null;
var assetScans    = {};  // assetId → { cond, output, dataKey }

// ── Build watchlist ────────────────────────────────────────────────────────
function buildWatchlist() {
  var panel = document.getElementById("wl-panel");
  var legend = panel.querySelector(".wl-legend");
  WATCHLIST_INDEX.assets.forEach(function(asset) {
    var div = document.createElement("div");
    div.className = "wl-asset";
    div.id = "wl-" + asset.id;
    div.onclick = function() { selectAsset(asset.id); };
    div.innerHTML =
      '<div class="wl-row">' +
        '<div class="wl-dot" id="dot-' + asset.id + '"></div>' +
        '<div>' +
          '<div class="wl-name">' + esc(asset.display_name) + '</div>' +
          '<div class="wl-type">' + esc(asset.type_label) + ' · ' + esc(asset.exchange) + '</div>' +
        '</div>' +
      '</div>' +
      '<div id="wl-summary-' + asset.id + '"></div>';
    panel.insertBefore(div, legend);
  });
}

// ── Asset selection ────────────────────────────────────────────────────────
function selectAsset(assetId) {
  selectedCond = null;
  selectedAsset = assetId;

  // Highlight selected row
  document.querySelectorAll(".wl-asset").forEach(function(el){el.classList.remove("selected");});
  var row = document.getElementById("wl-" + assetId);
  if (row) row.classList.add("selected");

  // Find asset definition
  var asset = WATCHLIST_INDEX.assets.find(function(a){return a.id===assetId;});
  if (!asset) return;

  // Update header
  document.getElementById("ctrl-asset-name").textContent = asset.display_name;
  document.getElementById("ctrl-asset-type").textContent =
    asset.type_label + " · " + asset.exchange + " · " + asset.applicable_detectors.join(", ");

  // Build ctrl-body
  var body = document.getElementById("ctrl-body");
  body.innerHTML = "";

  // Description
  var desc = document.createElement("div");
  desc.style.cssText = "font-size:12px;color:var(--t2);line-height:1.65;border-left:2px solid var(--b3);padding:8px 14px";
  desc.textContent = asset.description;
  body.appendChild(desc);

  // Condition picker label
  var cl = document.createElement("div");
  cl.className = "cond-label";
  cl.textContent = "Select market condition:";
  body.appendChild(cl);

  // Condition buttons
  var btns = document.createElement("div");
  btns.className = "cond-btns";
  btns.id = "cond-btns";
  asset.conditions.forEach(function(cond) {
    var btn = document.createElement("button");
    btn.className = "cond-btn";
    btn.id = "cbtn-" + cond.id;
    btn.textContent = cond.label;
    btn.onclick = function() { selectCond(assetId, cond); };
    btns.appendChild(btn);
  });
  body.appendChild(btns);

  // Condition description placeholder
  var cdesc = document.createElement("div");
  cdesc.className = "cond-desc";
  cdesc.id = "cond-desc";
  cdesc.textContent = "Click a condition to see what data will be fed into the detector.";
  body.appendChild(cdesc);

  // Spacer
  var sp = document.createElement("div");
  sp.style.flex = "1";
  body.appendChild(sp);

  // Run button (disabled until condition selected)
  var runBtn = document.createElement("button");
  runBtn.className = "run-btn";
  runBtn.id = "run-btn";
  runBtn.disabled = true;
  runBtn.innerHTML =
    '<div class="spinner"></div>' +
    '<span class="btn-text">Run Surveillance Scan</span>';
  runBtn.onclick = runScan;
  body.appendChild(runBtn);

  // Data legend
  var note = document.createElement("div");
  note.style.cssText = "font-size:10px;color:var(--t4);font-style:italic;margin-top:2px";
  note.textContent = "Scan loads pre-computed detector output from demo/sample_data/watchlist_" + assetId + "_{condition}.json";
  body.appendChild(note);

  // Restore previous result if re-selecting a scanned asset
  var prev = assetScans[assetId];
  if (prev) {
    showResult(prev.output, asset);
    // Re-highlight condition
    if (prev.cond) {
      var prevBtn = document.getElementById("cbtn-" + prev.cond);
      if (prevBtn) {
        prevBtn.classList.add("active");
        selectedCond = prev.cond;
        document.getElementById("run-btn").disabled = false;
        var condObj = asset.conditions.find(function(c){return c.id===prev.cond;});
        if (condObj) document.getElementById("cond-desc").textContent = condObj.description;
      }
    }
  } else {
    resetResultPanel();
  }
}

// ── Condition selection ────────────────────────────────────────────────────
function selectCond(assetId, cond) {
  selectedCond = cond.id;
  document.querySelectorAll(".cond-btn").forEach(function(b){b.classList.remove("active");});
  var btn = document.getElementById("cbtn-" + cond.id);
  if (btn) btn.classList.add("active");
  var cdesc = document.getElementById("cond-desc");
  if (cdesc) cdesc.textContent = cond.description;
  var runBtn = document.getElementById("run-btn");
  if (runBtn) runBtn.disabled = false;
}

// ── Run scan ───────────────────────────────────────────────────────────────
function runScan() {
  if (!selectedAsset || !selectedCond) return;

  var key = selectedAsset + "_" + selectedCond;
  var data = WATCHLIST_DATA[key];
  if (!data) {
    alert("ERROR: No data file found for " + key + ". This is a bug — this combination should not be selectable.");
    return;
  }

  var asset = WATCHLIST_INDEX.assets.find(function(a){return a.id===selectedAsset;});
  var runBtn = document.getElementById("run-btn");

  // Show scanning state
  runBtn.classList.add("running");
  runBtn.disabled = true;
  showScanning(data);

  // Simulate pipeline: feed data → run detector → show result
  setTimeout(function() {
    showInputData(data);
    setTimeout(function() {
      runBtn.classList.remove("running");
      runBtn.disabled = false;

      var out = data.output;
      // Cache the result
      assetScans[selectedAsset] = { cond: selectedCond, output: out, dataKey: key };

      // Update watchlist dot + summary
      updateWatchlistRow(selectedAsset, out);

      // Show result
      showResult(out, asset);
    }, 900);
  }, 600);
}

// ── Scanning animation (center panel) ─────────────────────────────────────
function showScanning(data) {
  var body = document.getElementById("ctrl-body");
  // Remove any existing viz sections
  var existing = document.getElementById("viz-section");
  if (existing) existing.remove();

  var vizSec = document.createElement("div");
  vizSec.id = "viz-section";
  vizSec.className = "viz-section";

  var inp = data.input || {};
  var d = inp.data || {};
  var dtype = d.type || "data";
  var detectors = (data.meta && data.meta.detector) ? data.meta.detector : "detector";

  vizSec.innerHTML =
    '<div class="viz-hdr">' +
      '<span class="viz-title">Feeding data into ' + esc(detectors) + '</span>' +
      '<span class="bdg bs">sample data</span>' +
    '</div>' +
    '<div class="scan-progress">' +
      '<div class="scan-step active"><span class="scan-ico">⟳</span>Loading ' + esc(dtype) + ' (' + esc(d.count || (d.records && d.records.length) || "—") + ' records)...</div>' +
    '</div>';

  // Insert before the spacer (before the run button row)
  var runBtn = document.getElementById("run-btn");
  runBtn.parentNode.insertBefore(vizSec, runBtn.parentNode.querySelector("div[style*='flex: 1']") || runBtn);
}

// ── Input data visualization ───────────────────────────────────────────────
function showInputData(data) {
  var vizSec = document.getElementById("viz-section");
  if (!vizSec) return;

  var inp = data.input || {};
  var d = inp.data || {};
  var records = d.records || d.orders || d.trades || [];
  var dtype = d.type || "";
  var detectors = (data.meta && data.meta.detector) ? data.meta.detector : "detector";

  var html =
    '<div class="viz-hdr">' +
      '<span class="viz-title">' + esc(d.description || dtype) + '</span>' +
      '<span class="bdg bs">sample data</span>' +
    '</div>' +
    '<div class="viz-anim">';

  if (records.length === 0) {
    html += '<div class="data-tape"><em style="color:var(--t4)">See input data in the JSON file.</em></div>';
  } else if (dtype === "price_snapshot") {
    // Key/value table
    html += '<div class="data-tape">';
    records.forEach(function(r) {
      var val = r.value !== undefined ? r.value : r.field;
      html += '<div class="tape-row">' +
        '<div class="tape-col" style="width:160px;color:var(--t3)">' + esc(r.field) + '</div>' +
        '<div class="tape-col tape-fired">' + esc(val) + '</div>' +
        '</div>';
    });
    html += '</div>';
  } else if (dtype === "option_chain") {
    // Group CE/PE
    html += '<div class="data-tape">';
    html += '<div class="tape-row tape-hdr">' +
      '<div class="tape-col" style="width:70px">Strike</div>' +
      '<div class="tape-col" style="width:36px">Type</div>' +
      '<div class="tape-col">OI (contracts)</div></div>';
    var maxOI = Math.max.apply(null, records.map(function(r){return r.oi||0;}));
    records.forEach(function(r) {
      var bar = "";
      if (maxOI > 0) {
        var pct = Math.round((r.oi/maxOI)*30);
        bar = '<span style="color:var(--b3)">│</span>' +
              '<span style="color:var(--re)">' + "█".repeat(pct) + '</span>';
      }
      html += '<div class="tape-row">' +
        '<div class="tape-col" style="width:70px">' + esc(r.strike) + '</div>' +
        '<div class="tape-col" style="width:36px;color:' + (r.option_type==="CE"?"#22d3ee":"#f97316") + '">' + esc(r.option_type) + '</div>' +
        '<div class="tape-col">' + Number(r.oi).toLocaleString() + ' ' + bar + '</div>' +
        '</div>';
    });
    html += '</div>';
  } else {
    // Order/trade tape
    var cols = Object.keys(records[0] || {});
    html += '<div class="data-tape">';
    html += '<div class="tape-row tape-hdr">' +
      cols.map(function(c){return '<div class="tape-col" style="min-width:60px">' + esc(c) + '</div>';}).join("") +
      '</div>';
    records.forEach(function(r) {
      var status = (r.status || "").toUpperCase();
      var rowCls = status.indexOf("CANCEL") !== -1 ? " tape-cancelled" :
                   status === "EXECUTED" ? " tape-executed" : "";
      html += '<div class="tape-row' + rowCls + '">' +
        cols.map(function(c){
          var v = r[c] !== undefined ? r[c] : "";
          return '<div class="tape-col" style="min-width:60px">' + esc(v) + '</div>';
        }).join("") +
        '</div>';
    });
    html += '</div>';
  }
  html += '</div>';  // close viz-anim

  // Scan step complete
  html = html.replace('<div class="scan-progress">', '');  // already removed above
  vizSec.innerHTML = html;
}

// ── Result panel ───────────────────────────────────────────────────────────
function resetResultPanel() {
  var body = document.getElementById("res-body");
  body.innerHTML = '<div class="res-placeholder">Run a scan to see detector output here.</div>';
}

function showResult(out, asset) {
  var body = document.getElementById("res-body");
  if (!out) { body.innerHTML = '<div class="res-placeholder">No result data available.</div>'; return; }

  var html = "";

  if (out.fired) {
    var sev = out.severity || "low";
    var det = out.detector || "";
    html +=
      '<div class="alert-card">' +
        '<div class="alert-card-hdr">' +
          '<span class="' + sevClass(sev) + '">' + esc(sev.toUpperCase()) + '</span>' +
          '<div class="alert-score">' + (typeof out.score==="number" ? out.score.toFixed(3) : "—") + '</div>' +
        '</div>' +
        '<div class="alert-det">' + esc(out.detector || "—") + '</div>' +
        '<div class="alert-expl">' + esc(out.explanation || "") + '</div>';

    // Evidence table
    if (out.evidence_table && out.evidence_table.rows && out.evidence_table.rows.length) {
      html += '<div class="ev-label">Evidence</div><div class="ev-wrap"><table class="ev-table"><thead><tr>';
      (out.evidence_table.columns || []).forEach(function(c) {
        html += '<th>' + esc(c) + '</th>';
      });
      html += '</tr></thead><tbody>';
      out.evidence_table.rows.forEach(function(row) {
        html += '<tr>';
        row.forEach(function(cell) {
          var cstr = String(cell || "");
          var cls = cstr.indexOf("FLAGGED") !== -1 || cstr.indexOf("DOMINANT") !== -1 ? ' class="flag"' : '';
          html += '<td' + cls + '>' + esc(cstr) + '</td>';
        });
        html += '</tr>';
      });
      html += '</tbody></table></div>';
    }

    // Source note
    html += '<div style="padding:8px 14px;font-size:10px;color:var(--t4);font-style:italic">' +
      'Real output of: <code>' + esc((out.call_signature||"").substring(0,80)) + '</code>' +
    '</div>';

    // Low score note
    if (typeof out.score === "number" && out.score < 0.45) {
      html += '<div style="padding:6px 14px;font-size:10px;color:var(--sy);background:var(--sb);border-top:1px solid var(--sd)">' +
        'Score &lt; 0.45 — below auto-escalation threshold. ' +
        'Detector thresholds documented as UNVALIDATED GUESS in source — needs calibration.' +
      '</div>';
    }

    html += '</div>';
  } else {
    // Clean result
    var detectors = out.detectors_run || [];
    html = '<div class="clean-card">' +
      '<div class="clean-icon">✓</div>' +
      '<div class="clean-title">No anomalies detected in this window</div>';

    var rbyd = out.results_by_detector || {};
    detectors.forEach(function(det) {
      var r = rbyd[det] || {};
      html += '<div class="clean-det-row">' +
        '<div class="clean-det-name">' + esc(det) + '</div>' +
        '<div>' + esc(r.reason || "returned None / empty list") + '</div>' +
      '</div>';
    });

    html += '<div class="caveat-note">' +
      'The detector function was called and returned no signal. ' +
      'This means the detector ran — it is not a blank screen.' +
    '</div>';
    html += '</div>';
  }

  body.innerHTML = html;
}

// ── Watchlist row update ───────────────────────────────────────────────────
function updateWatchlistRow(assetId, out) {
  var dot = document.getElementById("dot-" + assetId);
  var summaryEl = document.getElementById("wl-summary-" + assetId);
  if (!dot || !summaryEl) return;

  if (!out) return;

  if (out.fired) {
    var sev = out.severity || "low";
    dot.className = dotClass(sev);
    summaryEl.innerHTML = '<div class="wl-summary">' + esc(out.summary || "Alert detected") + '</div>';
  } else {
    dot.className = "wl-dot clean";
    summaryEl.innerHTML = '<div class="wl-clean-lbl">✓ No anomalies detected</div>';
  }
}

// ── Init ───────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", function() {
  buildWatchlist();
  // Auto-select first asset for UX
  if (WATCHLIST_INDEX.assets.length > 0) {
    selectAsset(WATCHLIST_INDEX.assets[0].id);
  }
});
</script>
</body>
</html>
"""

OUT.write_text(HTML, encoding="utf-8")
sz = OUT.stat().st_size
print(f"Written: {OUT}")
print(f"Size: {sz:,} bytes ({sz // 1024} KB)")
