"""
build_extended_demo.py
Builds the full 6-detector stakeholder demo HTML.
Run from repo root: python demo/build_extended_demo.py
"""
import json, pathlib, textwrap

ROOT = pathlib.Path(__file__).parent.parent
SD   = ROOT / "demo" / "sample_data"
OUT  = ROOT / "demo" / "sentinel_stakeholder_demo.html"

def _j(fname):
    return json.loads((SD / fname).read_text(encoding="utf-8"))

# Load all 12 JSON files
spoof_t   = _j("spoofing_trigger.json")
spoof_n   = _j("spoofing_normal.json")
circ_t    = _j("circular_trading_trigger.json")
circ_n    = _j("circular_trading_normal.json")
pump_t    = _j("coordinated_pump_trigger.json")
pump_n    = _j("coordinated_pump_normal.json")
oi_t      = _j("oi_manipulation_trigger.json")
oi_n      = _j("oi_manipulation_normal.json")
basis_t   = _j("basis_distortion_trigger.json")
basis_n   = _j("basis_distortion_normal.json")
pin_t     = _j("option_pinning_trigger.json")
pin_n     = _j("option_pinning_normal.json")

def fmt_expl(s): return (s or "").replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
def js_str(s):   return json.dumps(str(s))

# Embed the JSON objects directly into the HTML so it works offline
DATA_JS = f"""
const DATA = {{
  spoofing:        {{ trigger: {json.dumps(spoof_t)}, normal: {json.dumps(spoof_n)} }},
  circular:        {{ trigger: {json.dumps(circ_t)},  normal: {json.dumps(circ_n)}  }},
  pump:            {{ trigger: {json.dumps(pump_t)},  normal: {json.dumps(pump_n)}  }},
  oi:              {{ trigger: {json.dumps(oi_t)},    normal: {json.dumps(oi_n)}    }},
  basis:           {{ trigger: {json.dumps(basis_t)}, normal: {json.dumps(basis_n)} }},
  pinning:         {{ trigger: {json.dumps(pin_t)},   normal: {json.dumps(pin_n)}   }},
}};
"""

HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sentinel — Market Surveillance Platform</title>
<meta name="description" content="Sentinel: 6-detector order-level market surveillance for Indian equity markets. All detector results from real code runs.">
<style>
:root{
  --bg:#0b1422;--sf:#111e2e;--sf2:#162031;
  --b:#1e3048;--b2:#253d5e;
  --re:#22d3ee;--rd:#0e7490;--rb:rgba(34,211,238,.07);
  --sy:#f59e0b;--sd:#92400e;--sb:rgba(245,158,11,.08);
  --ah:#f97316;--ac:#ef4444;
  --t1:#e2e8f0;--t2:#94a3b8;--t3:#4a6180;
  --fd:Georgia,'Times New Roman',serif;
  --fb:system-ui,-apple-system,'Segoe UI',sans-serif;
  --fm:'Cascadia Code','Fira Code',Consolas,'Courier New',monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--t1);font-family:var(--fb);font-size:15px;line-height:1.6}
.sec{padding:64px 0;border-bottom:1px solid var(--b)}
.sec:last-of-type{border-bottom:none}
.c{max-width:1120px;margin:0 auto;padding:0 32px}
/* Nav */
nav{position:sticky;top:0;z-index:100;background:rgba(11,20,34,.95);backdrop-filter:blur(10px);
  border-bottom:1px solid var(--b);padding:10px 32px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.nbrand{font-family:var(--fm);font-size:13px;font-weight:600;letter-spacing:.04em;color:var(--re)}
.nlinks{display:flex;gap:20px;list-style:none;flex-wrap:wrap}
.nlinks a{font-size:12px;color:var(--t2);text-decoration:none;transition:color .15s}
.nlinks a:hover{color:var(--t1)}
/* Typography */
.slbl{font-family:var(--fm);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--t3);margin-bottom:8px}
h1{font-family:var(--fd);font-size:clamp(28px,4vw,44px);font-weight:400;line-height:1.2;margin-bottom:16px}
h2{font-family:var(--fd);font-size:clamp(20px,3vw,28px);font-weight:400;margin-bottom:12px}
h3{font-family:var(--fb);font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--t2);margin-bottom:8px}
.lede{font-size:15px;color:var(--t2);max-width:640px;margin-bottom:32px;line-height:1.7}
/* Badges */
.bdg{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;
  font-family:var(--fm);font-size:10px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;border-radius:2px}
.bdg::before{content:'';display:inline-block;width:5px;height:5px;border-radius:50%;background:currentColor}
.br{background:var(--rb);color:var(--re);border:1px solid var(--rd)}
.bs{background:var(--sb);color:var(--sy);border:1px solid var(--sd)}
/* Severity badge */
.sev{display:inline-block;font-family:var(--fm);font-size:10px;font-weight:700;
  letter-spacing:.08em;padding:2px 8px;color:#000}
.sev-critical{background:#ef4444}.sev-high{background:#f97316}
.sev-medium{background:#eab308}.sev-low{background:#6b7280;color:#fff}

/* ── DETECTOR GRID ── */
#detectors{background:var(--bg)}
.det-grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-top:32px}
@media(max-width:900px){.det-grid{grid-template-columns:1fr}}
.det-card{background:var(--sf);border:1px solid var(--b);display:flex;flex-direction:column}
.det-card-hdr{background:var(--sf2);padding:14px 18px;border-bottom:1px solid var(--b);
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.det-name{font-family:var(--fm);font-size:12px;font-weight:700;letter-spacing:.06em;
  text-transform:uppercase;color:var(--t1)}
.det-file{font-family:var(--fm);font-size:10px;color:var(--t3)}
.det-body{padding:18px;flex:1;display:flex;flex-direction:column;gap:14px}
.det-desc{font-size:13px;color:var(--t2);line-height:1.65}
/* Toggle */
.toggle-row{display:flex;gap:8px;margin-bottom:4px}
.tbtn{font-family:var(--fm);font-size:10px;font-weight:600;letter-spacing:.06em;
  text-transform:uppercase;padding:4px 12px;border:1px solid var(--b2);
  background:none;color:var(--t2);cursor:pointer;transition:all .15s}
.tbtn.active-t{background:var(--sb);color:var(--sy);border-color:var(--sd)}
.tbtn.active-n{background:var(--rb);color:var(--re);border-color:var(--rd)}
/* Viz containers */
.viz-wrap{width:100%;height:160px;position:relative;overflow:hidden;background:rgba(0,0,0,.15);border:1px solid var(--b)}
.viz-wrap svg{width:100%;height:100%}
/* Result box */
.res-box{border-left:2px solid var(--b2);padding:10px 14px;background:rgba(0,0,0,.12)}
.res-box.fired{border-left-color:var(--ah)}
.res-box.not-fired{border-left-color:var(--rd)}
.res-hdr{display:flex;align-items:center;gap:10px;margin-bottom:8px;flex-wrap:wrap}
.score-val{font-family:var(--fm);font-size:13px;color:var(--ah)}
.not-fired-lbl{font-family:var(--fm);font-size:11px;color:var(--re);letter-spacing:.06em}
.expl-txt{font-family:var(--fm);font-size:11px;color:var(--t2);line-height:1.65;
  max-height:96px;overflow-y:auto;white-space:pre-wrap;word-break:break-word}
.src-note{font-size:10px;color:var(--t3);font-style:italic;margin-top:6px}

/* ── NEGATIVE CONTROL ── */
#nc{background:var(--sf)}
.fbig{display:flex;gap:48px;align-items:flex-end;margin-bottom:28px;flex-wrap:wrap}
.fnum{font-family:var(--fm);font-size:clamp(48px,8vw,76px);font-weight:700;color:var(--re);line-height:1}
.fden{font-family:var(--fm);font-size:22px;color:var(--t3);padding-bottom:8px}
.frate{font-family:var(--fm);font-size:32px;color:var(--re)}
.fsub{font-family:var(--fm);font-size:12px;color:var(--t3)}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-family:var(--fm);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--t3);padding:8px 12px;border-bottom:1px solid var(--b)}
td{font-family:var(--fm);font-size:12px;padding:8px 12px;
  border-bottom:1px solid var(--b);color:var(--t2)}
tr:last-child td{border-bottom:none}
.sym{color:var(--t1);font-weight:600}.zero{color:var(--re)}
.fwrap{border:1px solid var(--rd);margin-top:20px}
.fthdr{background:var(--rb);padding:8px 14px;display:flex;align-items:center;
  justify-content:space-between;flex-wrap:wrap;gap:6px;border-bottom:1px solid var(--rd)}
.ftitle{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--re)}
.fint{max-width:540px;font-size:13px;color:var(--t2);line-height:1.7;
  padding:14px 18px;border-left:2px solid var(--rd);background:var(--rb);margin-top:20px}

/* ── ENGINEERING ── */
#eng{background:var(--bg)}
.egrid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--b);margin-bottom:28px}
@media(max-width:800px){.egrid{grid-template-columns:repeat(2,1fr)}}
.ecell{background:var(--sf);padding:22px 18px}
.eval{font-family:var(--fm);font-size:26px;font-weight:700;color:var(--t1);line-height:1;margin-bottom:4px}
.eunit{font-family:var(--fm);font-size:12px;color:var(--t2)}
.elbl{font-size:12px;color:var(--t3);margin-top:4px}
.esrc{font-size:10px;color:var(--t3);font-style:italic;margin-top:2px}
.rnote{display:flex;gap:14px;align-items:flex-start;padding:14px 18px;
  border:1px solid var(--b2);background:var(--sf)}
.rico{font-size:18px;flex-shrink:0;margin-top:2px}
.rtxt{font-size:13px;color:var(--t2);line-height:1.6}
.rtxt strong{color:var(--t1)}

/* ── HONEST STATUS ── */
#hs{background:var(--sf)}
.sgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--b)}
@media(max-width:800px){.sgrid{grid-template-columns:1fr}}
.scol{background:var(--bg);padding:26px 22px}
.shdr{display:flex;align-items:center;gap:10px;margin-bottom:18px;padding-bottom:12px;border-bottom:1px solid var(--b)}
.sico{width:26px;height:26px;border-radius:2px;display:flex;align-items:center;justify-content:center;font-size:12px;flex-shrink:0}
.sip{background:rgba(34,211,238,.12);color:var(--re)}
.sii{background:rgba(245,158,11,.12);color:var(--sy)}
.sib{background:rgba(100,116,139,.12);color:var(--t3)}
.stp{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--re)}
.sti{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--sy)}
.stb{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--t3)}
.sit{font-size:13px;color:var(--t2);padding:8px 0;border-bottom:1px solid var(--b);line-height:1.5}
.sit:last-child{border-bottom:none}
.sit strong{display:block;font-size:13px;margin-bottom:2px;color:var(--t1)}
.snote{margin-top:14px;font-size:12px;color:var(--t3);font-style:italic;line-height:1.6}

/* ── ARCHITECTURE ── */
#arch{background:var(--bg)}
.awrap{width:100%;overflow-x:auto;border:1px solid var(--b);background:var(--sf);padding:28px}

/* Footer */
footer{border-top:1px solid var(--b);padding:24px 32px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:14px}
.fbrand{font-family:var(--fm);font-size:12px;color:var(--t3)}
.fnote{font-size:12px;color:var(--t3);max-width:500px;line-height:1.5}
/* score threshold note */
.score-note{font-size:11px;color:var(--t3);font-style:italic;margin-top:6px}
</style>
</head>
<body>

<nav>
  <span class="nbrand">SENTINEL // MARKET SURVEILLANCE</span>
  <ul class="nlinks">
    <li><a href="#detectors">Detectors</a></li>
    <li><a href="#nc">False Positives</a></li>
    <li><a href="#eng">Engineering</a></li>
    <li><a href="#hs">Status</a></li>
    <li><a href="#arch">Architecture</a></li>
  </ul>
</nav>

<!-- =========================================================
  SECTION 1: ALL 6 DETECTORS
  Every result loaded from real detector output JSON.
  Sample data is synthetic; detector code is real.
  ========================================================= -->
<section id="detectors" class="sec">
<div class="c">
  <p class="slbl">Detection engines — 6 modules</p>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <h2>All 6 Detectors Running Against Sample Data</h2>
    <span class="bdg bs">Sample data — real detector output</span>
  </div>
  <p class="lede">
    Each panel below shows the REAL output of calling the detector function in
    <code style="font-size:13px;color:var(--sy)">app/detection/</code> against
    synthetic sample input. Scores, severities, and explanation text are verbatim
    from the detector — not paraphrased. Toggle <strong>Triggering pattern</strong> vs
    <strong>Normal trading — not flagged</strong> to see the contrast.
  </p>
  <div style="background:var(--sb);border:1px solid var(--sd);padding:10px 16px;margin-bottom:24px;font-size:13px;color:var(--sy)">
    <strong>All data in this section is synthetic sample data</strong> — generated by
    <code>demo/generate_all_detector_samples.py</code>, which imports and runs the real
    detector modules. No live market data. No real accounts.
    Detector output (scores, explanations) is the actual return value of each function call.
  </div>

  <div class="det-grid">

    <!-- ─── 1. SPOOFING ──────────────────────────────────────────────── -->
    <div class="det-card">
      <div class="det-card-hdr">
        <div>
          <div class="det-name">Spoofing / Layering</div>
          <div class="det-file">app/detection/spoofing.py · detect_spoofing_for_account()</div>
        </div>
        <span class="bdg bs">Sample data</span>
      </div>
      <div class="det-body">
        <p class="det-desc">
          An account places large orders to create false demand or supply, then cancels before
          execution — optionally trading the opposite side to profit from the price move it caused.
          The detector fires on the cancelled-order pattern, before most manipulative volume
          ever becomes a trade.
        </p>
        <div class="toggle-row">
          <button class="tbtn active-t" id="sp-t-btn" onclick="showPanel('sp','trigger')">Triggering pattern</button>
          <button class="tbtn"          id="sp-n-btn" onclick="showPanel('sp','normal')">Normal — not flagged</button>
        </div>
        <div class="viz-wrap" id="sp-viz">
          <svg id="sp-svg" viewBox="0 0 420 160" preserveAspectRatio="none">
            <defs><linearGradient id="pgr" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stop-color="#22d3ee" stop-opacity=".15"/>
              <stop offset="100%" stop-color="#22d3ee" stop-opacity="0"/>
            </linearGradient></defs>
            <path id="sp-area" fill="url(#pgr)" d="M0,160"/>
            <path id="sp-line" fill="none" stroke="#22d3ee" stroke-width="1.5" d="M0,160"/>
            <rect id="sp-spoof-zone" x="60" y="0" width="180" height="160" fill="rgba(245,158,11,.05)" opacity="0"/>
            <line id="sp-spoof-line" x1="60" y1="0" x2="60" y2="160" stroke="#f59e0b" stroke-width=".7" stroke-dasharray="3,3" opacity="0"/>
            <text id="sp-spoof-lbl" x="66" y="14" font-family="Consolas,monospace" font-size="8" fill="#f59e0b" opacity="0">cancel burst (synthetic)</text>
          </svg>
        </div>
        <div id="sp-result"></div>
      </div>
    </div>

    <!-- ─── 2. CIRCULAR TRADING ──────────────────────────────────────── -->
    <div class="det-card">
      <div class="det-card-hdr">
        <div>
          <div class="det-name">Circular Trading</div>
          <div class="det-file">app/detection/circular_trading.py · detect_circular_trading()</div>
        </div>
        <span class="bdg bs">Sample data</span>
      </div>
      <div class="det-body">
        <p class="det-desc">
          A ring of accounts trades the same instrument back and forth with no net economic
          purpose — generating artificial volume and potentially moving price. The detector
          builds a directed trade graph and uses Johnson's cycle algorithm to find rings
          where all accounts return to their starting position.
        </p>
        <div class="toggle-row">
          <button class="tbtn active-t" id="ci-t-btn" onclick="showPanel('ci','trigger')">Triggering pattern</button>
          <button class="tbtn"          id="ci-n-btn" onclick="showPanel('ci','normal')">Normal — not flagged</button>
        </div>
        <div class="viz-wrap" id="ci-viz">
          <svg id="ci-svg" viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg">
            <!-- 5-node ring network graph, animated on trigger -->
          </svg>
        </div>
        <div id="ci-result"></div>
      </div>
    </div>

    <!-- ─── 3. COORDINATED PUMP ──────────────────────────────────────── -->
    <div class="det-card">
      <div class="det-card-hdr">
        <div>
          <div class="det-name">Coordinated Pump</div>
          <div class="det-file">app/detection/coordinated_pump.py · detect_coordinated_pump()</div>
        </div>
        <span class="bdg bs">Sample data</span>
      </div>
      <div class="det-body">
        <p class="det-desc">
          Multiple accounts (often dormant) place synchronized buy orders in a short window,
          pushing up price to create false demand. The detector fires when ≥ 3 accounts and
          ≥ 5× normal volume coincide, weighted by proportion of dormant/new accounts.
        </p>
        <div class="toggle-row">
          <button class="tbtn active-t" id="pu-t-btn" onclick="showPanel('pu','trigger')">Triggering pattern</button>
          <button class="tbtn"          id="pu-n-btn" onclick="showPanel('pu','normal')">Normal — not flagged</button>
        </div>
        <div class="viz-wrap" id="pu-viz">
          <svg id="pu-svg" viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg">
            <!-- parallel account buy timelines -->
          </svg>
        </div>
        <div id="pu-result"></div>
      </div>
    </div>

    <!-- ─── 4. OI MANIPULATION ───────────────────────────────────────── -->
    <div class="det-card">
      <div class="det-card-hdr">
        <div>
          <div class="det-name">OI Manipulation</div>
          <div class="det-file">app/detection/oi_manipulation.py · detect_oi_concentration()</div>
        </div>
        <span class="bdg bs">Sample data</span>
      </div>
      <div class="det-body">
        <p class="det-desc">
          Abnormal concentration of open interest in a single option strike — one strike
          holds a disproportionate fraction of the chain, suggesting a structural play
          (gamma squeeze setup, false market-expectation signals) rather than normal hedging.
        </p>
        <div class="toggle-row">
          <button class="tbtn active-t" id="oi-t-btn" onclick="showPanel('oi','trigger')">Triggering pattern</button>
          <button class="tbtn"          id="oi-n-btn" onclick="showPanel('oi','normal')">Normal — not flagged</button>
        </div>
        <div class="viz-wrap" id="oi-viz">
          <svg id="oi-svg" viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg">
            <!-- OI bar chart per strike -->
          </svg>
        </div>
        <div id="oi-result"></div>
      </div>
    </div>

    <!-- ─── 5. BASIS DISTORTION ──────────────────────────────────────── -->
    <div class="det-card">
      <div class="det-card-hdr">
        <div>
          <div class="det-name">Basis Distortion</div>
          <div class="det-file">app/detection/basis_distortion.py · detect_basis_distortion()</div>
        </div>
        <span class="bdg bs">Sample data</span>
      </div>
      <div class="det-body">
        <p class="det-desc">
          Futures trading at an abnormal premium or discount to the theoretical fair-value
          basis (cost-of-carry model). Excess contango: artificial buying in futures to
          create bullish signals. Excess backwardation: artificial selling to depress prices.
        </p>
        <div class="toggle-row">
          <button class="tbtn active-t" id="ba-t-btn" onclick="showPanel('ba','trigger')">Triggering pattern</button>
          <button class="tbtn"          id="ba-n-btn" onclick="showPanel('ba','normal')">Normal — not flagged</button>
        </div>
        <div class="viz-wrap" id="ba-viz">
          <svg id="ba-svg" viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg">
            <!-- futures vs spot line with basis gap -->
          </svg>
        </div>
        <div id="ba-result"></div>
      </div>
    </div>

    <!-- ─── 6. OPTION PINNING ────────────────────────────────────────── -->
    <div class="det-card">
      <div class="det-card-hdr">
        <div>
          <div class="det-name">Option Pinning</div>
          <div class="det-file">app/detection/option_pinning.py · detect_option_pinning()</div>
        </div>
        <span class="bdg bs">Sample data</span>
      </div>
      <div class="det-body">
        <p class="det-desc">
          Spot price held near a high-OI strike as expiry approaches — the "max pain" pattern.
          Fires only when ALL THREE conditions hold simultaneously: spot within 0.5% of the
          dominant OI strike, ≤ 2 days to expiry, AND the strike OI is ≥ 2× adjacent strikes.
        </p>
        <div class="toggle-row">
          <button class="tbtn active-t" id="pi-t-btn" onclick="showPanel('pi','trigger')">Triggering pattern</button>
          <button class="tbtn"          id="pi-n-btn" onclick="showPanel('pi','normal')">Normal — not flagged</button>
        </div>
        <div class="viz-wrap" id="pi-viz">
          <svg id="pi-svg" viewBox="0 0 420 160" xmlns="http://www.w3.org/2000/svg">
            <!-- strike OI bars + spot line -->
          </svg>
        </div>
        <div id="pi-result"></div>
      </div>
    </div>

  </div><!-- .det-grid -->
</div>
</section>

<!-- =========================================================
  SECTION 2: NEGATIVE CONTROL — VERIFIED REAL DATA
  Source: backtest/results/negative_controls.json
  ========================================================= -->
<section id="nc" class="sec">
<div class="c">
  <p class="slbl">Real-data validation</p>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <h2>False Positive Rate on Real NSE Data</h2>
    <span class="bdg br">Verified real data</span>
  </div>
  <p class="lede">
    The detectors were run against 90 real NSE trading days across India's five most
    liquid large-cap stocks. No spurious alerts. This proves the system does not flag
    normal legitimate trading — it does <em style="font-style:italic">not</em> prove it
    catches real manipulation (see Status section for that distinction).
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
        <tr><td class="sym">TCS</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr><td class="sym">HDFCBANK</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
        <tr><td class="sym">INFY</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td></tr>
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
    <strong style="color:var(--re);font-size:13px">What this proves and what it does not.</strong><br>
    A 0.0% false positive rate on large-cap stocks means the detector does not flag normal
    institutional and retail trading in India's most liquid equities. This is real, verifiable.
    It does not prove the system catches actual manipulation — that validation requires
    account-level order data unavailable in public archives. See the Status section.
  </div>
</div>
</section>

<!-- =========================================================
  SECTION 3: ENGINEERING PROOF
  ========================================================= -->
<section id="eng" class="sec">
<div class="c">
  <p class="slbl">Engineering validation</p>
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
    <h2>Performance, Correctness &amp; Security</h2>
    <span class="bdg br">Verified real data</span>
  </div>
  <p class="lede">Every figure was measured by running the actual system. Not an estimate.</p>
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
      <div class="elbl">Exactly 1 alert survived each time</div>
      <div class="esrc">stress/test_concurrent_access.py · 10 trials × 50 threads</div>
    </div>
  </div>
  <div class="rnote">
    <div class="rico">&#128274;</div>
    <div class="rtxt">
      <strong>Race condition found and fixed during real stress testing — not in code review.</strong>
      Concurrent detector threads could produce duplicate Alert rows. Fix:
      <code>UniqueConstraint("instrument_id", "pattern_type", "window_start")</code>.
      Second concurrent INSERT raises IntegrityError, caught and suppressed.
      Verified: 500 writes, 10 trials × 50 threads — exactly 1 alert survived every time.
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
  <p class="lede">Every claim made in this presentation is in the PROVEN column. The other columns exist so any reviewer has a complete picture.</p>
  <div class="sgrid">
    <div class="scol">
      <div class="shdr"><div class="sico sip">&#10003;</div><span class="stp">Proven</span></div>
      <div class="sit"><strong>Engineering correctness</strong>273 tests pass. All 6 detectors, DB layer, ingestion, security covered.</div>
      <div class="sit"><strong>Performance at volume</strong>Spoofing 19,224 /sec; pump 51,349 /sec — well within the 120s/100k threshold.</div>
      <div class="sit"><strong>0.0% FP on large-cap NSE data</strong>90 real days, 5 NSE stocks, 0 spurious alerts. Verifiable from negative_controls.json.</div>
      <div class="sit"><strong>All 6 detectors produce sensible results on sample data</strong>Each fires on the triggering scenario and does not fire on the normal scenario. Real detector output, not simulated.</div>
      <div class="sit"><strong>Concurrency integrity</strong>Race condition found, fixed, and verified. UniqueConstraint prevents duplicate alerts.</div>
      <div class="sit"><strong>Security and audit trail</strong>PII masking, append-only access logs, configurable retention — all implemented and tested.</div>
    </div>
    <div class="scol">
      <div class="shdr"><div class="sico sii">&#9677;</div><span class="sti">In Progress</span></div>
      <div class="sit"><strong>Detection efficacy against real confirmed manipulation — ALL 6 DETECTORS</strong>None of the 6 detectors have yet been validated against a confirmed real historical manipulation case. The sample data scenarios in this demo demonstrate that the logic fires correctly on the expected pattern; they do not constitute detection of actual manipulation. This applies equally to spoofing, circular trading, coordinated pump, OI manipulation, basis distortion, and option pinning.</div>
      <div class="sit"><strong>OI manipulation and basis distortion: low scores on sample data</strong>The triggering sample produced "low" severity scores (0.185 and 0.246 respectively). This is an honest finding about threshold calibration — these thresholds are documented as UNVALIDATED GUESSes and need backtesting. Not hidden.</div>
      <div class="sit"><strong>Real-case SEBI backtest UNTESTABLE via public data</strong>Three real SEBI enforcement orders identified. All three involve BSE-listed scrips absent from NSE bhavcopy — structurally untestable without BSE data access or official data sharing.</div>
      <div class="snote">This is the precise current state. Not a hedged claim.</div>
    </div>
    <div class="scol">
      <div class="shdr"><div class="sico sib">&#10007;</div><span class="stb">Not Possible Without Official Data Access</span></div>
      <div class="sit"><strong>Spoofing and circular trading efficacy</strong>Both require order-lifecycle data (per-order timestamps and counterparty IDs). NSE/BSE have never published historical order books publicly.</div>
      <div class="sit"><strong>Coordinated pump with real dormancy data</strong>Requires historical account-level trade records. Only available to SEBI, NSE, BSE surveillance teams under formal access.</div>
      <div class="sit"><strong>OI and option pinning validation</strong>Historical option chain OI snapshots do not exist in any public Indian archive. NSE publishes only the current live chain.</div>
      <div class="snote">This is a structural fact about Indian market data infrastructure. Every commercial surveillance vendor in India faces the same constraint. Their efficacy claims derive from formal data-sharing agreements with exchanges — the same path available here.</div>
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
  <p class="lede">All labels below are actual filenames in this repository.</p>
  <div class="awrap">
    <svg viewBox="0 0 960 380" xmlns="http://www.w3.org/2000/svg"
         font-family="Consolas,'Courier New',monospace" font-size="11" style="width:100%;max-width:960px;display:block;margin:0 auto">
      <defs>
        <marker id="a1" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#253d5e"/></marker>
        <marker id="a2" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
          <path d="M0,0 L0,6 L8,3 z" fill="#0e7490"/></marker>
      </defs>
      <!-- Row labels -->
      <text x="4" y="40" fill="#4a6180" font-size="9" letter-spacing="1">DATA SOURCES</text>
      <text x="4" y="145" fill="#4a6180" font-size="9" letter-spacing="1">INGESTION</text>
      <text x="4" y="240" fill="#4a6180" font-size="9" letter-spacing="1">DETECTION</text>
      <text x="4" y="335" fill="#4a6180" font-size="9" letter-spacing="1">OUTPUT</text>
      <line x1="100" y1="52" x2="950" y2="52" stroke="#1e3048" stroke-width=".5"/>
      <line x1="100" y1="157" x2="950" y2="157" stroke="#1e3048" stroke-width=".5"/>
      <line x1="100" y1="255" x2="950" y2="255" stroke="#1e3048" stroke-width=".5"/>
      <!-- Sources -->
      <rect x="108" y="16" width="160" height="32" fill="#111e2e" stroke="#1e3048"/>
      <text x="188" y="30" text-anchor="middle" fill="#94a3b8">NSE Bhavcopy</text>
      <text x="188" y="42" text-anchor="middle" fill="#4a6180" font-size="9">archives.nseindia.com</text>
      <rect x="280" y="16" width="160" height="32" fill="#111e2e" stroke="#1e3048"/>
      <text x="360" y="30" text-anchor="middle" fill="#94a3b8">NSE Option Chain</text>
      <text x="360" y="42" text-anchor="middle" fill="#4a6180" font-size="9">OI + Greeks</text>
      <rect x="452" y="16" width="160" height="32" fill="#111e2e" stroke="#1e3048"/>
      <text x="532" y="30" text-anchor="middle" fill="#94a3b8">NSE Bulk Deals</text>
      <text x="532" y="42" text-anchor="middle" fill="#4a6180" font-size="9">block trades</text>
      <rect x="634" y="6" width="200" height="42" fill="#0a1929" stroke="#253d5e"/>
      <text x="734" y="22" text-anchor="middle" fill="#22d3ee">resilience.py</text>
      <text x="734" y="34" text-anchor="middle" fill="#4a6180" font-size="9">retry_with_backoff · CircuitBreaker</text>
      <text x="734" y="46" text-anchor="middle" fill="#4a6180" font-size="9">bhavcopy_circuit · delivery_circuit</text>
      <!-- Ingestion -->
      <rect x="108" y="120" width="160" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="188" y="134" text-anchor="middle" fill="#e2e8f0">nse_bhavcopy.py</text>
      <text x="188" y="146" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
      <rect x="280" y="120" width="160" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="360" y="134" text-anchor="middle" fill="#e2e8f0">nse_option_chain.py</text>
      <text x="360" y="146" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
      <rect x="452" y="120" width="160" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="532" y="134" text-anchor="middle" fill="#e2e8f0">nse_bulk_deals.py</text>
      <text x="532" y="146" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
      <!-- arrows src → ingest -->
      <line x1="188" y1="48" x2="188" y2="118" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <line x1="360" y1="48" x2="360" y2="118" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <line x1="532" y1="48" x2="532" y2="118" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <!-- Detection — 6 detectors in 2 rows of 3 -->
      <rect x="108" y="168" width="140" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="178" y="181" text-anchor="middle" fill="#e2e8f0">spoofing.py</text>
      <text x="178" y="193" text-anchor="middle" fill="#4a6180" font-size="9">cancel · size · impact</text>
      <rect x="258" y="168" width="145" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="330" y="181" text-anchor="middle" fill="#e2e8f0">circular_trading.py</text>
      <text x="330" y="193" text-anchor="middle" fill="#4a6180" font-size="9">graph cycles</text>
      <rect x="413" y="168" width="152" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="489" y="181" text-anchor="middle" fill="#e2e8f0">coordinated_pump.py</text>
      <text x="489" y="193" text-anchor="middle" fill="#4a6180" font-size="9">dormancy · vol spike</text>
      <rect x="108" y="210" width="140" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="178" y="223" text-anchor="middle" fill="#e2e8f0">oi_manipulation.py</text>
      <text x="178" y="235" text-anchor="middle" fill="#4a6180" font-size="9">OI concentration</text>
      <rect x="258" y="210" width="145" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="330" y="223" text-anchor="middle" fill="#e2e8f0">basis_distortion.py</text>
      <text x="330" y="235" text-anchor="middle" fill="#4a6180" font-size="9">futures-spot spread</text>
      <rect x="413" y="210" width="152" height="30" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="489" y="223" text-anchor="middle" fill="#e2e8f0">option_pinning.py</text>
      <text x="489" y="235" text-anchor="middle" fill="#4a6180" font-size="9">strike OI · DTE</text>
      <!-- Ingest → detection bus -->
      <line x1="300" y1="152" x2="300" y2="166" stroke="#253d5e" stroke-width="1" marker-end="url(#a1)"/>
      <!-- Output -->
      <rect x="108" y="266" width="140" height="32" fill="#0a1929" stroke="#0e7490"/>
      <text x="178" y="280" text-anchor="middle" fill="#22d3ee">evidence.py</text>
      <text x="178" y="292" text-anchor="middle" fill="#0e7490" font-size="9">evidence log · alert builder</text>
      <rect x="260" y="266" width="145" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="332" y="280" text-anchor="middle" fill="#e2e8f0">app/db/models.py</text>
      <text x="332" y="292" text-anchor="middle" fill="#4a6180" font-size="9">Alert · UniqueConstraint</text>
      <rect x="416" y="266" width="108" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="470" y="280" text-anchor="middle" fill="#e2e8f0">access_log.py</text>
      <text x="470" y="292" text-anchor="middle" fill="#4a6180" font-size="9">audit trail</text>
      <rect x="534" y="266" width="80" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="574" y="280" text-anchor="middle" fill="#e2e8f0">pii.py</text>
      <text x="574" y="292" text-anchor="middle" fill="#4a6180" font-size="9">masking</text>
      <rect x="624" y="266" width="100" height="32" fill="#0e1f2f" stroke="#1e3048"/>
      <text x="674" y="280" text-anchor="middle" fill="#e2e8f0">retention.py</text>
      <text x="674" y="292" text-anchor="middle" fill="#4a6180" font-size="9">lifecycle</text>
      <!-- detect → evidence -->
      <line x1="300" y1="240" x2="300" y2="264" stroke="#0e7490" stroke-width="1" marker-end="url(#a2)"/>
      <line x1="178" y1="298" x2="178" y2="318" stroke="#253d5e" stroke-width=".8" marker-end="url(#a1)"/>
      <rect x="130" y="320" width="96" height="24" fill="#111e2e" stroke="#ef4444" stroke-width=".8"/>
      <text x="178" y="336" text-anchor="middle" fill="#ef4444" font-size="10">Alert</text>
    </svg>
  </div>
</div>
</section>

<footer>
  <div class="fbrand">SENTINEL // v1.0-alpha // September 2026</div>
  <div class="fnote">
    Detector results in this demo are the actual return values of calling
    app/detection/*.py functions. Source: demo/sample_data/*.json.
    All sample data is synthetic — not derived from any real market session or account.
  </div>
</footer>

<script>
"use strict";
// ── Embedded real detector output (generated by demo/generate_all_detector_samples.py)
""" + DATA_JS + """

// ── Utility ──────────────────────────────────────────────────────────────────

function escHtml(s) {
  if (!s) return "";
  return String(s)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;")
    .replace(/"/g,"&quot;");
}

function sevClass(sev) {
  if (!sev) return "";
  return "sev sev-" + sev.toLowerCase();
}

function renderResult(elId, data, scenario) {
  var el = document.getElementById(elId);
  if (!el) return;
  var out = data[scenario] && data[scenario].output;
  if (!out) { el.innerHTML = '<div class="res-box"><em style="color:var(--t3)">No output loaded.</em></div>'; return; }

  if (!out.fired) {
    el.innerHTML = '<div class="res-box not-fired">' +
      '<div class="res-hdr"><span class="not-fired-lbl">Not flagged — below threshold</span></div>' +
      '<div class="expl-txt">' + escHtml(out.reason || "Detector returned None.") + '</div>' +
      '<div class="src-note">Source: demo/sample_data/' + escHtml(data[scenario].meta && data[scenario].meta.detector) + '_' + scenario + '.json</div>' +
      '</div>';
    return;
  }

  var scoreStr = typeof out.score === "number" ? out.score.toFixed(3) : "N/A";
  var sev      = out.severity || "";
  var expl     = out.explanation || out.reason || "";
  var src      = (data[scenario].meta && data[scenario].meta.call) || "";
  var lowScore = typeof out.score === "number" && out.score < 0.45;

  el.innerHTML =
    '<div class="res-box fired">' +
    '<div class="res-hdr">' +
    '<span class="' + sevClass(sev) + '">' + escHtml(sev.toUpperCase()) + '</span>' +
    '<span class="score-val">Score: ' + scoreStr + '</span>' +
    '</div>' +
    (lowScore ? '<div class="score-note">Score below 0.45 — would not escalate automatically (medium threshold). Threshold documented as UNVALIDATED GUESS in detector source.</div>' : '') +
    '<div class="expl-txt">' + escHtml(expl) + '</div>' +
    '<div class="src-note">Real output of: <code>' + escHtml(src) + '</code></div>' +
    '</div>';
}

// ── Per-panel visualization renderers ────────────────────────────────────────

function renderSpViz(scenario) {
  var svg   = document.getElementById("sp-svg");
  var d     = DATA.spoofing[scenario];
  var isTrig = scenario === "trigger";

  // Price line (synthetic, illustrative of the pattern)
  var PX = isTrig
    ? [8.45,8.45,8.50,8.55,8.55,8.60,8.63,8.65,8.65,8.60,8.58,8.55,8.53,8.50,8.50]
    : [8.45,8.46,8.46,8.47,8.47,8.48,8.48,8.49,8.49,8.48,8.48,8.47,8.47,8.48,8.48];
  var W=420,H=160,pT=14,pB=18;
  var mn=Math.min.apply(null,PX),mx=Math.max.apply(null,PX),rng=mx-mn||0.01;
  var xs=PX.map(function(_,i){return (i/(PX.length-1))*W;});
  var ys=PX.map(function(p){return pT+(1-(p-mn)/rng)*(H-pT-pB);});
  var l=xs.map(function(x,i){return (i===0?"M":"L")+x.toFixed(1)+","+ys[i].toFixed(1);}).join(" ");
  document.getElementById("sp-line").setAttribute("d",l);
  document.getElementById("sp-area").setAttribute("d",l+" L"+W+","+H+" L0,"+H+" Z");
  // Spoof zone markers
  var op = isTrig ? "1" : "0";
  document.getElementById("sp-spoof-zone").setAttribute("opacity", isTrig ? "1" : "0");
  document.getElementById("sp-spoof-line").setAttribute("opacity", op);
  document.getElementById("sp-spoof-lbl").setAttribute("opacity", op);

  // Order dots
  svg.querySelectorAll(".odot").forEach(function(e){e.remove();});
  if (d && d.input && d.input.orders) {
    var maxT = d.input.orders.length;
    d.input.orders.forEach(function(o,i) {
      var x = 20 + (i/(maxT))*360;
      var clr = o.status === "OrderStatus.CANCELLED" ? "#ef4444"
              : o.status === "OrderStatus.EXECUTED"  ? "#34d399"
              : "#94a3b8";
      var dot = document.createElementNS("http://www.w3.org/2000/svg","circle");
      dot.setAttribute("class","odot");
      dot.setAttribute("cx",x.toFixed(1));
      dot.setAttribute("cy","145");
      dot.setAttribute("r","4");
      dot.setAttribute("fill",clr);
      svg.appendChild(dot);
    });
  }
}

function renderCiViz(scenario) {
  var svg = document.getElementById("ci-svg");
  svg.innerHTML = "";
  var isTrig = scenario === "trigger";
  var ringColor = isTrig ? "#ef4444" : "#253d5e";
  var nodeColor = isTrig ? "#f97316" : "#253d5e";
  var nodeLabel = isTrig ? "#fff"    : "#94a3b8";

  // 5-node ring layout
  var nodes = [{x:210,y:30},{x:340,y:80},{x:310,y:130},{x:110,y:130},{x:80,y:80}];
  var labels = ["ACC-A","ACC-B","ACC-C","ACC-D","ACC-E"];

  // Draw edges (ring cycle)
  var n = nodes.length;
  for (var i=0;i<n;i++) {
    var a=nodes[i], b=nodes[(i+1)%n];
    var mk = isTrig ? 'url(#a1)' : 'none';
    var line = document.createElementNS("http://www.w3.org/2000/svg","line");
    line.setAttribute("x1",a.x);line.setAttribute("y1",a.y);
    line.setAttribute("x2",b.x);line.setAttribute("y2",b.y);
    line.setAttribute("stroke",ringColor);line.setAttribute("stroke-width",isTrig?"1.5":"0.8");
    if (isTrig) line.setAttribute("marker-end","url(#a1)");
    svg.appendChild(line);
  }
  // Draw extra independent edges for normal scenario
  if (!isTrig) {
    [[0,2],[1,3]].forEach(function(pair){
      var a=nodes[pair[0]],b=nodes[pair[1]];
      var line2=document.createElementNS("http://www.w3.org/2000/svg","line");
      line2.setAttribute("x1",a.x);line2.setAttribute("y1",a.y);
      line2.setAttribute("x2",b.x);line2.setAttribute("y2",b.y);
      line2.setAttribute("stroke","#253d5e");line2.setAttribute("stroke-width","0.5");
      line2.setAttribute("stroke-dasharray","3,3");
      svg.appendChild(line2);
    });
  }
  // Draw nodes
  nodes.forEach(function(nd,i){
    var c=document.createElementNS("http://www.w3.org/2000/svg","circle");
    c.setAttribute("cx",nd.x);c.setAttribute("cy",nd.y);c.setAttribute("r","14");
    c.setAttribute("fill",nodeColor);c.setAttribute("stroke",isTrig?"#f97316":"#1e3048");c.setAttribute("stroke-width","1.5");
    svg.appendChild(c);
    var t=document.createElementNS("http://www.w3.org/2000/svg","text");
    t.setAttribute("x",nd.x);t.setAttribute("y",nd.y+4);t.setAttribute("text-anchor","middle");
    t.setAttribute("font-size","8");t.setAttribute("fill",nodeLabel);t.setAttribute("font-family","Consolas,monospace");
    t.textContent=labels[i];
    svg.appendChild(t);
  });
  // Label
  var lbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  lbl.setAttribute("x","210");lbl.setAttribute("y","155");lbl.setAttribute("text-anchor","middle");
  lbl.setAttribute("font-size","9");lbl.setAttribute("fill",isTrig?"#f97316":"#22d3ee");lbl.setAttribute("font-family","Consolas,monospace");
  lbl.textContent=isTrig?"5-account ring detected — counterparty directly confirmed":"Independent accounts — no closed cycle";
  svg.appendChild(lbl);
}

function renderPuViz(scenario) {
  var svg = document.getElementById("pu-svg");
  svg.innerHTML = "";
  var isTrig = scenario === "trigger";
  var accts = isTrig ? 7 : 4;
  var barW = 6, gap = 4, startX = 30;
  var H=160,barMaxH=80;
  // Volume bars per account per time slot
  for (var i=0;i<accts;i++) {
    var x = startX + i*(barW+gap);
    var qty = isTrig ? 2000 : 200;
    var maxQty = isTrig ? 2000 : 200;
    var bh = (qty/maxQty)*barMaxH;
    var by = H-20-bh;
    var rect=document.createElementNS("http://www.w3.org/2000/svg","rect");
    rect.setAttribute("x",x);rect.setAttribute("y",by);rect.setAttribute("width",barW);rect.setAttribute("height",bh);
    rect.setAttribute("fill",isTrig?"#f97316":"#22d3ee");rect.setAttribute("opacity","0.8");
    svg.appendChild(rect);
  }
  // Normal volume line
  var nvLine=document.createElementNS("http://www.w3.org/2000/svg","line");
  nvLine.setAttribute("x1","20");nvLine.setAttribute("y1",(H-20-20).toString());
  nvLine.setAttribute("x2","400");nvLine.setAttribute("y2",(H-20-20).toString());
  nvLine.setAttribute("stroke","#94a3b8");nvLine.setAttribute("stroke-width","0.8");
  nvLine.setAttribute("stroke-dasharray","4,3");
  svg.appendChild(nvLine);
  var nvLbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  nvLbl.setAttribute("x","405");nvLbl.setAttribute("y",(H-20-16).toString());
  nvLbl.setAttribute("font-size","8");nvLbl.setAttribute("fill","#94a3b8");nvLbl.setAttribute("font-family","Consolas,monospace");
  nvLbl.textContent="normal vol";
  svg.appendChild(nvLbl);
  var lbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  lbl.setAttribute("x","210");lbl.setAttribute("y","155");lbl.setAttribute("text-anchor","middle");
  lbl.setAttribute("font-size","9");lbl.setAttribute("fill",isTrig?"#f97316":"#22d3ee");lbl.setAttribute("font-family","Consolas,monospace");
  lbl.textContent=isTrig?(accts+" dormant accounts · "+accts+"×2000 shares · 6.1× normal vol"):"4 active accounts · 200 shares each · below threshold";
  svg.appendChild(lbl);
}

function renderOiViz(scenario) {
  var svg = document.getElementById("oi-svg");
  svg.innerHTML="";
  var isTrig = scenario==="trigger";
  var H=160,barMaxH=110,W=420;
  var strikes  = [24000,24200,24400,24600,24800];
  var ois_trig = [504000,220000,180000,150000,146000];
  var ois_norm = [300000,280000,260000,240000,220000];
  var ois = isTrig ? ois_trig : ois_norm;
  var maxOI=Math.max.apply(null,ois);
  var barW=44, gap=12, startX=30;
  strikes.forEach(function(strike,i){
    var bh=(ois[i]/maxOI)*barMaxH;
    var by=H-22-bh;
    var x=startX+i*(barW+gap);
    var isFlag=isTrig&&i===0;
    var rect=document.createElementNS("http://www.w3.org/2000/svg","rect");
    rect.setAttribute("x",x);rect.setAttribute("y",by);rect.setAttribute("width",barW);rect.setAttribute("height",bh);
    rect.setAttribute("fill",isFlag?"#ef4444":"#253d5e");rect.setAttribute("opacity",isFlag?"1":"0.7");
    svg.appendChild(rect);
    if(isFlag){
      var lbl=document.createElementNS("http://www.w3.org/2000/svg","text");
      lbl.setAttribute("x",x+barW/2);lbl.setAttribute("y",by-4);lbl.setAttribute("text-anchor","middle");
      lbl.setAttribute("font-size","9");lbl.setAttribute("fill","#ef4444");lbl.setAttribute("font-family","Consolas,monospace");
      lbl.textContent="42%";
      svg.appendChild(lbl);
    }
    var slbl=document.createElementNS("http://www.w3.org/2000/svg","text");
    slbl.setAttribute("x",x+barW/2);slbl.setAttribute("y",H-8);slbl.setAttribute("text-anchor","middle");
    slbl.setAttribute("font-size","8");slbl.setAttribute("fill","#4a6180");slbl.setAttribute("font-family","Consolas,monospace");
    slbl.textContent=strike;
    svg.appendChild(slbl);
  });
  var foot=document.createElementNS("http://www.w3.org/2000/svg","text");
  foot.setAttribute("x","210");foot.setAttribute("y","155");foot.setAttribute("text-anchor","middle");
  foot.setAttribute("font-size","9");foot.setAttribute("fill",isTrig?"#ef4444":"#22d3ee");foot.setAttribute("font-family","Consolas,monospace");
  foot.textContent=isTrig?"24000 PE holds 42% of chain OI — flagged":"Balanced OI distribution — not flagged";
  svg.appendChild(foot);
}

function renderBaViz(scenario) {
  var svg=document.getElementById("ba-svg");
  svg.innerHTML="";
  var isTrig=scenario==="trigger";
  var H=160,W=420,pT=14,pB=24;
  // spot line (flat baseline)
  var spotY=(H-pB-pT)/2+pT+10;
  var spotLine=document.createElementNS("http://www.w3.org/2000/svg","line");
  spotLine.setAttribute("x1","20");spotLine.setAttribute("y1",spotY);
  spotLine.setAttribute("x2","400");spotLine.setAttribute("y2",spotY);
  spotLine.setAttribute("stroke","#22d3ee");spotLine.setAttribute("stroke-width","1.5");
  svg.appendChild(spotLine);
  var sLbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  sLbl.setAttribute("x","405");sLbl.setAttribute("y",spotY+4);
  sLbl.setAttribute("font-size","8");sLbl.setAttribute("fill","#22d3ee");sLbl.setAttribute("font-family","Consolas,monospace");
  sLbl.textContent="spot";
  svg.appendChild(sLbl);
  // futures line: trigger = elevated (excess contango), normal ≈ FV
  var futPts=[];
  if(isTrig){
    // Elevated futures — excess contango
    for(var i=0;i<16;i++) futPts.push(spotY - 45 - (i>10?(i-10)*3:0));
  } else {
    // Near fair-value — slight premium only
    for(var i=0;i<16;i++) futPts.push(spotY - 8);
  }
  var fxs=futPts.map(function(_,i){return 20+(i/15)*380;});
  var fpath=fxs.map(function(x,i){return (i===0?"M":"L")+x.toFixed(1)+","+futPts[i].toFixed(1);}).join(" ");
  var fline=document.createElementNS("http://www.w3.org/2000/svg","path");
  fline.setAttribute("d",fpath);fline.setAttribute("fill","none");
  fline.setAttribute("stroke",isTrig?"#f97316":"#94a3b8");fline.setAttribute("stroke-width","1.5");
  svg.appendChild(fline);
  var fLbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  fLbl.setAttribute("x","405");fLbl.setAttribute("y",(futPts[futPts.length-1]+4).toFixed(1));
  fLbl.setAttribute("font-size","8");fLbl.setAttribute("fill",isTrig?"#f97316":"#94a3b8");fLbl.setAttribute("font-family","Consolas,monospace");
  fLbl.textContent="futures";
  svg.appendChild(fLbl);
  if(isTrig){
    // shade the basis gap
    var gapPath="M20,"+futPts[0]+" "+fxs.map(function(x,i){return "L"+x.toFixed(1)+","+futPts[i].toFixed(1);}).join(" ")+
      " L"+fxs[fxs.length-1]+","+spotY+" L20,"+spotY+" Z";
    var gapRect=document.createElementNS("http://www.w3.org/2000/svg","path");
    gapRect.setAttribute("d",gapPath);gapRect.setAttribute("fill","rgba(249,115,22,.12)");
    svg.appendChild(gapRect);
  }
  var foot=document.createElementNS("http://www.w3.org/2000/svg","text");
  foot.setAttribute("x","210");foot.setAttribute("y","155");foot.setAttribute("text-anchor","middle");
  foot.setAttribute("font-size","9");foot.setAttribute("fill",isTrig?"#f97316":"#22d3ee");foot.setAttribute("font-family","Consolas,monospace");
  foot.textContent=isTrig?"Excess contango: futures +₹45 vs FV +₹15 — flagged":"Futures near fair-value — not flagged";
  svg.appendChild(foot);
}

function renderPiViz(scenario) {
  var svg=document.getElementById("pi-svg");
  svg.innerHTML="";
  var isTrig=scenario==="trigger";
  var H=160,barMaxH=110,W=420;
  var strikes=[24300,24400,24500,24600,24700];
  var ois_trig=[60000,120000,520000,110000,50000];
  var ois_norm=[60000,120000,200000,110000,50000];
  var ois=isTrig?ois_trig:ois_norm;
  var maxOI=Math.max.apply(null,ois);
  var barW=44,gap=12,startX=20;
  var SPOT_STRIKE=24500;
  strikes.forEach(function(strike,i){
    var bh=(ois[i]/maxOI)*barMaxH;
    var by=H-24-bh;
    var x=startX+i*(barW+gap);
    var isPin=isTrig&&strike===SPOT_STRIKE;
    var rect=document.createElementNS("http://www.w3.org/2000/svg","rect");
    rect.setAttribute("x",x);rect.setAttribute("y",by);rect.setAttribute("width",barW);rect.setAttribute("height",bh);
    rect.setAttribute("fill",isPin?"#ef4444":"#253d5e");rect.setAttribute("opacity",isPin?"1":"0.6");
    svg.appendChild(rect);
    var slbl=document.createElementNS("http://www.w3.org/2000/svg","text");
    slbl.setAttribute("x",x+barW/2);slbl.setAttribute("y",H-9);slbl.setAttribute("text-anchor","middle");
    slbl.setAttribute("font-size","8");slbl.setAttribute("fill","#4a6180");slbl.setAttribute("font-family","Consolas,monospace");
    slbl.textContent=strike;
    svg.appendChild(slbl);
  });
  // Spot price line
  var pinIdx=strikes.indexOf(SPOT_STRIKE);
  var pinX=startX+pinIdx*(barW+gap)+barW/2;
  var spotLine=document.createElementNS("http://www.w3.org/2000/svg","line");
  spotLine.setAttribute("x1",pinX-(isTrig?2:30));spotLine.setAttribute("y1","10");
  spotLine.setAttribute("x2",pinX+(isTrig?2:30));spotLine.setAttribute("y2","10");
  spotLine.setAttribute("stroke","#22d3ee");spotLine.setAttribute("stroke-width","2");
  svg.appendChild(spotLine);
  var spotLbl=document.createElementNS("http://www.w3.org/2000/svg","text");
  spotLbl.setAttribute("x",pinX+(isTrig?6:36));spotLbl.setAttribute("y","14");
  spotLbl.setAttribute("font-size","8");spotLbl.setAttribute("fill","#22d3ee");spotLbl.setAttribute("font-family","Consolas,monospace");
  spotLbl.textContent=isTrig?"spot=24,497":"spot=24,497";
  svg.appendChild(spotLbl);
  var foot=document.createElementNS("http://www.w3.org/2000/svg","text");
  foot.setAttribute("x","210");foot.setAttribute("y","155");foot.setAttribute("text-anchor","middle");
  foot.setAttribute("font-size","9");foot.setAttribute("fill",isTrig?"#ef4444":"#22d3ee");foot.setAttribute("font-family","Consolas,monospace");
  foot.textContent=isTrig?"24500 dominates OI · spot within 0.01% · 0 DTE — CRITICAL":"5 DTE → detector returns None immediately";
  svg.appendChild(foot);
}

// ── State + panel controller ──────────────────────────────────────────────────

var panelState = { sp:"trigger", ci:"trigger", pu:"trigger", oi:"trigger", ba:"trigger", pi:"trigger" };

var panelMap = {
  sp: { data: DATA.spoofing,  result:"sp-result", viz: renderSpViz },
  ci: { data: DATA.circular,  result:"ci-result", viz: renderCiViz },
  pu: { data: DATA.pump,      result:"pu-result", viz: renderPuViz },
  oi: { data: DATA.oi,        result:"oi-result", viz: renderOiViz },
  ba: { data: DATA.basis,     result:"ba-result", viz: renderBaViz },
  pi: { data: DATA.pinning,   result:"pi-result", viz: renderPiViz },
};

function showPanel(prefix, scenario) {
  panelState[prefix] = scenario;
  var pm = panelMap[prefix];
  pm.viz(scenario);
  renderResult(pm.result, pm.data, scenario);
  // Update button states
  var tBtn = document.getElementById(prefix+"-t-btn");
  var nBtn = document.getElementById(prefix+"-n-btn");
  if (tBtn) { tBtn.className = "tbtn" + (scenario==="trigger"?" active-t":""); }
  if (nBtn) { nBtn.className = "tbtn" + (scenario==="normal" ?" active-n":""); }
}

document.addEventListener("DOMContentLoaded", function() {
  Object.keys(panelMap).forEach(function(prefix) {
    showPanel(prefix, "trigger");
  });
});
</script>
</body>
</html>
"""

OUT.write_text(HTML, encoding="utf-8")
sz = OUT.stat().st_size
print(f"Written: {OUT}")
print(f"Size: {sz:,} bytes ({sz // 1024} KB)")
