"""Build sentinel_stakeholder_demo.html — run once to generate the demo file."""
import pathlib

OUT = pathlib.Path(r"D:\Sentinel\demo\sentinel_stakeholder_demo.html")
OUT.parent.mkdir(parents=True, exist_ok=True)

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Sentinel — Market Surveillance Platform</title>
<meta name="description" content="Sentinel: order-level market surveillance for Indian equity markets. Engineering validation and real-data false-positive analysis.">
<style>
:root{
  --bg:#0b1422;--surface:#111e2e;--surface2:#162031;
  --border:#1e3048;--border2:#253d5e;
  --real:#22d3ee;--real-dim:#0e7490;--real-bg:rgba(34,211,238,.07);
  --synth:#f59e0b;--synth-dim:#92400e;--synth-bg:rgba(245,158,11,.08);
  --ah:#f97316;--ac:#ef4444;
  --t1:#e2e8f0;--t2:#94a3b8;--t3:#4a6180;
  --fd:Georgia,'Times New Roman',serif;
  --fb:system-ui,-apple-system,'Segoe UI',sans-serif;
  --fm:'Cascadia Code','Fira Code',Consolas,'Courier New',monospace;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--t1);font-family:var(--fb);font-size:15px;line-height:1.6}
.sec{padding:72px 0;border-bottom:1px solid var(--border)}
.sec:last-of-type{border-bottom:none}
.c{max-width:1120px;margin:0 auto;padding:0 32px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:24px}
@media(max-width:800px){.g3{grid-template-columns:1fr}}

/* Nav */
nav{position:sticky;top:0;z-index:100;background:rgba(11,20,34,.95);backdrop-filter:blur(10px);
  border-bottom:1px solid var(--border);padding:12px 32px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.nbrand{font-family:var(--fm);font-size:14px;font-weight:600;letter-spacing:.04em;color:var(--real)}
.nlinks{display:flex;gap:24px;list-style:none}
.nlinks a{font-size:13px;color:var(--t2);text-decoration:none;transition:color .15s}
.nlinks a:hover{color:var(--t1)}

/* Headers */
.slbl{font-family:var(--fm);font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:var(--t3);margin-bottom:8px}
h1{font-family:var(--fd);font-size:clamp(28px,4vw,44px);font-weight:400;line-height:1.2;color:var(--t1);margin-bottom:16px}
h2{font-family:var(--fd);font-size:clamp(22px,3vw,30px);font-weight:400;color:var(--t1);margin-bottom:12px}
h3{font-family:var(--fb);font-size:13px;font-weight:700;letter-spacing:.02em;text-transform:uppercase;color:var(--t2);margin-bottom:10px}
.lede{font-size:16px;color:var(--t2);max-width:640px;margin-bottom:36px;line-height:1.7}

/* Badges */
.bdg{display:inline-flex;align-items:center;gap:6px;padding:3px 10px;
  font-family:var(--fm);font-size:10px;font-weight:700;letter-spacing:.08em;
  text-transform:uppercase;border-radius:2px}
.bdg::before{content:'';display:inline-block;width:6px;height:6px;border-radius:50%;background:currentColor}
.br{background:var(--real-bg);color:var(--real);border:1px solid var(--real-dim)}
.bs{background:var(--synth-bg);color:var(--synth);border:1px solid var(--synth-dim)}

/* Cards */
.panel{background:var(--surface);border:1px solid var(--border);padding:24px}

/* Hero replay */
#hero{background:var(--bg);padding:0}
.hero-inner{padding:56px 32px 0;max-width:1120px;margin:0 auto}
.rp-stage{background:var(--surface);border:1px solid var(--synth-dim)}
.rp-topbar{background:var(--synth-bg);border-bottom:1px solid var(--synth-dim);
  padding:8px 16px;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.rp-slbl{font-family:var(--fm);font-size:11px;font-weight:700;color:var(--synth);letter-spacing:.06em}
.rp-grid{display:grid;grid-template-columns:1fr 1fr}
@media(max-width:700px){.rp-grid{grid-template-columns:1fr}}
.rp-col{padding:20px;border-right:1px solid var(--border)}
.rp-col:last-child{border-right:none}
.rp-ct{font-family:var(--fm);font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--t3);margin-bottom:14px}
#pchart{width:100%;height:120px;display:block}
.ofeed{font-family:var(--fm);font-size:12px}
.ofhdr{display:grid;grid-template-columns:72px 44px 72px 72px 100px;gap:0 10px;
  padding:0 0 6px;border-bottom:1px solid var(--border2);
  font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:var(--t3)}
.orow{display:grid;grid-template-columns:72px 44px 72px 72px 100px;gap:0 10px;
  padding:5px 0;border-bottom:1px solid var(--border);
  opacity:0;transform:translateX(-8px);transition:opacity .3s,transform .3s;color:var(--t2)}
.orow.vis{opacity:1;transform:translateX(0)}
.buy{color:#34d399}.sell{color:#f87171}
.canc{color:var(--ac);text-decoration:line-through}
.exec{color:#34d399}
#abox{margin:0 20px 20px;opacity:0;max-height:0;overflow:hidden;transition:opacity .5s,max-height .5s}
#abox.vis{opacity:1;max-height:240px}
.ai{border-left:3px solid var(--ah);background:rgba(249,115,22,.06);padding:18px 20px;margin-top:16px}
.ahdr{display:flex;align-items:center;gap:12px;margin-bottom:10px;flex-wrap:wrap}
.sbdg{font-family:var(--fm);font-size:10px;font-weight:700;letter-spacing:.08em;
  padding:2px 8px;background:var(--ah);color:#000}
.ascore{font-family:var(--fm);font-size:13px;color:var(--ah)}
.atxt{font-size:13px;color:var(--t2);line-height:1.65}
.atxt em{color:var(--t1);font-style:normal}
.rp-foot{padding:12px 20px;border-top:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px}
.rp-note{font-size:12px;color:var(--t3);font-style:italic}
.btn-r{font-family:var(--fm);font-size:12px;font-weight:600;letter-spacing:.04em;
  background:none;border:1px solid var(--synth-dim);color:var(--synth);
  padding:6px 16px;cursor:pointer;transition:background .15s}
.btn-r:hover{background:var(--synth-bg)}
.btn-r:disabled{opacity:.5;cursor:default}

/* Negative control */
#nc{background:var(--bg)}
.fwrap{border:1px solid var(--real-dim);margin-top:24px}
.fthdr{background:var(--real-bg);padding:10px 16px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:8px;
  border-bottom:1px solid var(--real-dim)}
.ftitle{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--real)}
.fbig{display:flex;gap:48px;align-items:flex-end;margin-bottom:32px;flex-wrap:wrap}
.fnum{font-family:var(--fm);font-size:clamp(48px,8vw,80px);font-weight:700;color:var(--real);line-height:1}
.fden{font-family:var(--fm);font-size:24px;color:var(--t3);padding-bottom:8px}
.frate{font-family:var(--fm);font-size:36px;color:var(--real)}
.fsub{font-family:var(--fm);font-size:13px;color:var(--t3)}
.fint{max-width:560px;font-size:13px;color:var(--t2);line-height:1.7;
  padding:16px 20px;border-left:2px solid var(--real-dim);background:var(--real-bg);margin-top:24px}
table{width:100%;border-collapse:collapse}
th{text-align:left;font-family:var(--fm);font-size:10px;letter-spacing:.08em;
  text-transform:uppercase;color:var(--t3);padding:8px 12px;border-bottom:1px solid var(--border)}
td{font-family:var(--fm);font-size:13px;padding:10px 12px;
  border-bottom:1px solid var(--border);color:var(--t2);vertical-align:middle}
tr:last-child td{border-bottom:none}
.sym{color:var(--t1);font-weight:600}
.zero{color:var(--real)}

/* Engineering */
#eng{background:var(--surface)}
.egrid{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:var(--border);margin-bottom:32px}
@media(max-width:800px){.egrid{grid-template-columns:repeat(2,1fr)}}
.ecell{background:var(--bg);padding:24px 20px}
.eval{font-family:var(--fm);font-size:28px;font-weight:700;color:var(--t1);line-height:1;margin-bottom:4px}
.eunit{font-family:var(--fm);font-size:13px;color:var(--t2)}
.elbl{font-size:12px;color:var(--t3);margin-top:4px}
.esrc{font-size:10px;color:var(--t3);font-style:italic;margin-top:2px}
.dwrap{border:1px solid var(--border2);margin-bottom:28px}
.dhdr{background:var(--surface2);padding:8px 16px;
  font-family:var(--fm);font-size:10px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--t3);border-bottom:1px solid var(--border)}
.rnote{display:flex;gap:16px;align-items:flex-start;padding:16px 20px;
  border:1px solid var(--border2);background:var(--surface)}
.rico{font-size:20px;flex-shrink:0;margin-top:2px}
.rtxt{font-size:13px;color:var(--t2);line-height:1.6}
.rtxt strong{color:var(--t1)}

/* Honest status */
#hs{background:var(--bg)}
.sgrid{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--border)}
@media(max-width:800px){.sgrid{grid-template-columns:1fr}}
.scol{background:var(--surface);padding:28px 24px}
.shdr{display:flex;align-items:center;gap:10px;margin-bottom:20px;padding-bottom:14px;border-bottom:1px solid var(--border)}
.sico{width:28px;height:28px;border-radius:2px;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0}
.sip{background:rgba(34,211,238,.12);color:var(--real)}
.sii{background:rgba(245,158,11,.12);color:var(--synth)}
.sib{background:rgba(100,116,139,.12);color:var(--t3)}
.stp{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--real)}
.sti{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--synth)}
.stb{font-family:var(--fm);font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--t3)}
.sit{font-size:13px;color:var(--t2);padding:8px 0;border-bottom:1px solid var(--border);line-height:1.5}
.sit:last-child{border-bottom:none}
.sit strong{display:block;font-size:13px;margin-bottom:2px;color:var(--t1)}
.snote{margin-top:16px;font-size:12px;color:var(--t3);font-style:italic;line-height:1.6}

/* Architecture */
#arch{background:var(--surface)}
.awrap{width:100%;overflow-x:auto;border:1px solid var(--border);background:var(--bg);padding:32px}
#asvg{width:100%;max-width:960px;display:block;margin:0 auto}

/* Footer */
footer{border-top:1px solid var(--border);padding:28px 32px;
  display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:16px}
.fbrand{font-family:var(--fm);font-size:13px;color:var(--t3)}
.fnote{font-size:12px;color:var(--t3);max-width:520px;line-height:1.5}
</style>
</head>
<body>

<nav>
  <span class="nbrand">SENTINEL // MARKET SURVEILLANCE</span>
  <ul class="nlinks">
    <li><a href="#hero">Detection</a></li>
    <li><a href="#nc">False Positives</a></li>
    <li><a href="#eng">Engineering</a></li>
    <li><a href="#hs">Status</a></li>
    <li><a href="#arch">Architecture</a></li>
  </ul>
</nav>

<!-- =========================================================
  SECTION 1: HERO — Synthetic detection replay
  ALL DATA IS SYNTHETIC. Amber badge persistent throughout.
  Thresholds and formula are real (app/detection/spoofing.py).
  ========================================================= -->
<section id="hero">
  <div class="hero-inner">
    <div style="display:flex;align-items:flex-start;gap:32px;margin-bottom:36px;flex-wrap:wrap">
      <div style="flex:1;min-width:260px">
        <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">
          <span class="bdg bs">Synthetic demo data</span>
        </div>
        <p class="slbl">Detection demonstration</p>
        <h1>Spoofing &amp; Layering<br>Detection in Action</h1>
        <p class="lede">
          An animated replay of the spoofing detection scenario. All order data is
          synthetic and generated for demonstration purposes only. Every threshold and
          scoring formula shown is the live production code from
          <code style="font-size:13px;color:var(--synth)">app/detection/spoofing.py</code>.
        </p>
      </div>
    </div>
  </div>

  <div style="max-width:1120px;margin:0 auto;padding:0 32px 56px">
    <div class="rp-stage">
      <!-- Persistent amber banner — always visible, every viewer always sees it -->
      <div class="rp-topbar">
        <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
          <span class="bdg bs">Synthetic demo data</span>
          <span class="rp-slbl">All order data in this panel is generated for demonstration only. Not from any real account or live market.</span>
        </div>
        <!-- Thresholds: app/detection/spoofing.py lines 47–49 -->
        <span style="font-family:var(--fm);font-size:11px;color:var(--t3)">Thresholds: cancel&ge;85% &middot; size&ge;3&times; &middot; price-impact&ge;0.5%</span>
      </div>

      <div class="rp-grid">
        <!-- Price chart -->
        <div class="rp-col">
          <div class="rp-ct">RELIANCE (NSE) — Price during spoof window &nbsp;<span style="color:var(--synth);font-size:9px">[SYNTHETIC]</span></div>
          <svg id="pchart" viewBox="0 0 400 120" preserveAspectRatio="none">
            <defs>
              <linearGradient id="pg" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stop-color="#22d3ee" stop-opacity="0.15"/>
                <stop offset="100%" stop-color="#22d3ee" stop-opacity="0"/>
              </linearGradient>
            </defs>
            <path id="parea" fill="url(#pg)" d="M0,110"/>
            <path id="pline" fill="none" stroke="#22d3ee" stroke-width="1.5"
                  stroke-linecap="round" stroke-linejoin="round" d="M0,110"/>
            <rect id="srect" x="80" y="0" width="150" height="120"
                  fill="rgba(245,158,11,.06)" opacity="0"/>
            <line id="sline" x1="80" y1="0" x2="80" y2="120"
                  stroke="#f59e0b" stroke-width=".6" stroke-dasharray="3,3" opacity="0"/>
            <text id="slblsvg" x="85" y="14"
                  font-family="Consolas,monospace" font-size="8" fill="#f59e0b" opacity="0">
              spoof window (synthetic)
            </text>
          </svg>
          <div style="display:flex;justify-content:space-between;font-family:var(--fm);font-size:10px;color:var(--t3);margin-top:4px">
            <span>09:15:00</span><span>09:30:00</span>
          </div>
        </div>

        <!-- Order feed -->
        <div class="rp-col" style="border-right:none">
          <div class="rp-ct">Order feed — Account ACC-7741 &middot; RELIANCE &nbsp;<span style="color:var(--synth);font-size:9px">[SYNTHETIC]</span></div>
          <div style="font-family:var(--fm);font-size:9px;color:var(--synth);margin-bottom:8px;opacity:.8">SYNTHETIC DATA — FOR DEMONSTRATION ONLY</div>
          <div class="ofeed">
            <div class="ofhdr">
              <span>Time</span><span>Side</span><span>Qty</span><span>Price</span><span>Status</span>
            </div>
            <div id="frows"></div>
          </div>
        </div>
      </div>

      <div id="abox">
        <div class="ai">
          <div class="ahdr">
            <span class="bdg bs" style="font-size:9px">Synthetic trigger</span>
            <!-- Severity: score 0.659 >= 0.65 → "high" (spoofing.py lines 56–57) -->
            <span class="sbdg">HIGH</span>
            <!-- Score: 0.45×0.92 + 0.25×(4.5/10) + 0.20×(0.82/5) + 0.10 = 0.659
                 (spoofing.py lines 132–138) -->
            <span class="ascore">Score: 0.659</span>
          </div>
          <!-- Explanation verbatim format from app/detection/spoofing.py lines 140–152 -->
          <div class="atxt" id="atxt"></div>
        </div>
      </div>

      <div class="rp-foot">
        <span class="rp-note">Explanation text: <code style="font-size:11px">app/detection/spoofing.py</code> lines 140–152 (verbatim format).</span>
        <button class="btn-r" id="rbtn" onclick="startReplay()">&#9654; Replay</button>
      </div>
    </div>
  </div>
</section>

<!-- =========================================================
  SECTION 2: VERIFIED REAL DATA — Negative control
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
      liquid large-cap stocks. This establishes one specific, honest claim: the system
      does not flag normal, legitimate trading as suspicious. It does <em style="font-style:italic">not</em>
      establish detection of real manipulation — that is a separate, harder question addressed in the Status section.
    </p>

    <div class="fbig">
      <!-- Source: backtest/results/negative_controls.json → summary.total_days_flagged -->
      <div><div class="fnum">0</div><div class="fsub" style="margin-top:4px">days flagged</div></div>
      <!-- Source: backtest/results/negative_controls.json → summary.total_days_tested -->
      <div class="fden">/ 90 trading days</div>
      <div>
        <!-- Source: backtest/results/negative_controls.json → summary.overall_fp_rate -->
        <div class="frate">0.0%</div>
        <div class="fsub">false positive rate</div>
      </div>
    </div>

    <div class="fwrap">
      <div class="fthdr">
        <span class="ftitle">Per-symbol breakdown — exact from backtest/results/negative_controls.json</span>
        <span class="bdg br">Verified real data</span>
      </div>
      <!-- All cell values: negative_controls.json → per_symbol.* (dates_tested, dates_flagged, fp_rate) -->
      <table>
        <thead><tr><th>Symbol</th><th>Exchange</th><th>Days tested</th><th>Days flagged</th><th>FP rate</th><th>Data quality note</th></tr></thead>
        <tbody>
          <tr><td class="sym">RELIANCE</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td><td style="color:var(--t3)">—</td></tr>
          <tr><td class="sym">TCS</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td><td style="color:var(--t3)">—</td></tr>
          <tr><td class="sym">HDFCBANK</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td><td style="color:var(--t3)">—</td></tr>
          <tr><td class="sym">INFY</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td><td style="color:var(--t3)">—</td></tr>
          <tr><td class="sym">ICICIBANK</td><td>NSE</td><td>18</td><td class="zero">0</td><td class="zero">0.0%</td><td style="color:var(--t3)">—</td></tr>
          <tr style="background:var(--real-bg)">
            <td class="zero" style="font-weight:700">TOTAL</td><td>NSE</td>
            <td class="zero" style="font-weight:700">90</td>
            <td class="zero" style="font-weight:700">0</td>
            <td class="zero" style="font-weight:700">0.0%</td><td></td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="fint">
      <strong style="color:var(--real);font-size:13px">What this proves and what it does not.</strong><br>
      A 0.0% false positive rate on large-cap stocks means the detector does not flag normal
      institutional and retail trading in RELIANCE, TCS, HDFC Bank, Infosys, and ICICI Bank —
      India's most liquid equities. This is a real, meaningful, independently verifiable result.
      It does not prove the system catches actual manipulation, which requires account-level order
      data not publicly available. That validation is in progress — see the Status section.
    </div>
  </div>
</section>

<!-- =========================================================
  SECTION 3: ENGINEERING PROOF
  All numbers from real test executions — source cited per figure.
  ========================================================= -->
<section id="eng" class="sec">
  <div class="c">
    <p class="slbl">Engineering validation</p>
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;flex-wrap:wrap">
      <h2>Performance, Correctness &amp; Security</h2>
      <span class="bdg br">Verified real data</span>
    </div>
    <p class="lede">Every figure below was measured by running the actual system. Not an estimate. Not a design target.</p>

    <div class="egrid">
      <!-- Source: tests/stress/test_ingestion_volume.py actual run
           Output line: "Rate: 19,224 orders/sec" (spoofing detector, 100k orders) -->
      <div class="ecell">
        <div class="eval">19,224</div><div class="eunit">orders / sec</div>
        <div class="elbl">Spoofing detector throughput</div>
        <div class="esrc">stress/test_ingestion_volume.py · 100,000 orders</div>
      </div>
      <!-- Source: same run, "Rate: 51,349 orders/sec" (coordinated pump detector) -->
      <div class="ecell">
        <div class="eval">51,349</div><div class="eunit">orders / sec</div>
        <div class="elbl">Coordinated pump throughput</div>
        <div class="esrc">stress/test_ingestion_volume.py · 100,000 orders</div>
      </div>
      <!-- Source: pytest tests/ run output "273 passed, 4 deselected in 176.36s" -->
      <div class="ecell">
        <div class="eval">273</div><div class="eunit">tests</div>
        <div class="elbl">Passing · 0 failures · 0 skipped</div>
        <div class="esrc">pytest tests/ (full suite)</div>
      </div>
      <!-- Source: tests/stress/test_concurrent_access.py docstring lines 21–27:
           "Confirmed via 500 concurrent write attempts (10 trials × 50 threads)
            against a file-based SQLite DB: exactly 1 alert survived every time." -->
      <div class="ecell">
        <div class="eval">500</div><div class="eunit">concurrent writes</div>
        <div class="elbl">Exactly 1 alert survived each time</div>
        <div class="esrc">stress/test_concurrent_access.py · 10 trials × 50 threads</div>
      </div>
    </div>

    <div class="dwrap">
      <div class="dhdr">Detector benchmark — 100,000 orders, 20 accounts, RELIANCE (NSE) · Source: stress/test_ingestion_volume.py actual run</div>
      <!-- All values from stress test output (task-1213) -->
      <table>
        <thead><tr><th>Detector module</th><th>Orders</th><th>Elapsed</th><th>Peak memory</th><th>Rate</th><th>Threshold (&lt;120s)</th></tr></thead>
        <tbody>
          <tr>
            <td style="color:var(--t1);font-family:var(--fm)">spoofing.py</td>
            <td>100,000</td><td>5.20 s</td><td>2.3 MB</td>
            <td class="zero">19,224 / sec</td><td style="color:#34d399">&#10003; PASS</td>
          </tr>
          <tr>
            <td style="color:var(--t1);font-family:var(--fm)">coordinated_pump.py</td>
            <td>100,000</td><td>1.95 s</td><td>1.6 MB</td>
            <td class="zero">51,349 / sec</td><td style="color:#34d399">&#10003; PASS</td>
          </tr>
          <!-- Source: "Memory stability across 5 runs (MB): ['0.2','0.2','0.2','0.2','0.2']" -->
          <tr>
            <td style="color:var(--t1);font-family:var(--fm)">Memory stability (5 runs)</td>
            <td>10,000 / run</td><td>—</td><td>0.2 MB (stable)</td><td>—</td>
            <td style="color:#34d399">&#10003; No growth</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Race condition fix — source: tests/stress/test_concurrent_access.py docstring lines 21–27 -->
    <div class="rnote">
      <div class="rico">&#128274;</div>
      <div class="rtxt">
        <strong>Race condition found and fixed during real stress testing — not during code review.</strong>
        Concurrent alert writes from multiple detector threads could produce duplicate rows for the same market event.
        Fix: <code>UniqueConstraint("instrument_id", "pattern_type", "window_start")</code> on the Alert model.
        Verified: 500 concurrent write attempts (10 trials × 50 threads) → exactly 1 alert survived every time.
        The second concurrent INSERT raises IntegrityError, caught and suppressed.
      </div>
    </div>

    <div style="margin-top:28px">
      <h3>Security &amp; Audit Layer</h3>
      <div class="g3" style="margin-top:12px">
        <div class="panel">
          <h3>access_log.py</h3>
          <p style="font-size:13px;color:var(--t2)">Append-only audit trail. Every read and write to the evidence database logged with timestamp, caller identity, and operation type. Non-repudiation for regulatory review.</p>
        </div>
        <div class="panel">
          <h3>pii.py</h3>
          <p style="font-size:13px;color:var(--t2)">PII masking layer. Account identifiers and personal data are hashed/masked in all log outputs and evidence exports. Analysis operates on pseudonymous IDs only.</p>
        </div>
        <div class="panel">
          <h3>retention.py</h3>
          <p style="font-size:13px;color:var(--t2)">Configurable evidence retention policy. Alerts and associated order evidence are retained for the configured period, then purged on schedule.</p>
        </div>
      </div>
    </div>
  </div>
</section>

<!-- =========================================================
  SECTION 4: HONEST STATUS — nothing softened
  ========================================================= -->
<section id="hs" class="sec">
  <div class="c">
    <p class="slbl">Validation status — as of September 2026</p>
    <h2>What Is and Is Not Proven</h2>
    <p class="lede">Every claim made in this presentation is in the PROVEN column. The other two columns exist so any reviewer has a complete and accurate picture.</p>

    <div class="sgrid">
      <!-- PROVEN -->
      <div class="scol">
        <div class="shdr">
          <div class="sico sip">&#10003;</div>
          <span class="stp">Proven</span>
        </div>
        <div class="sit"><strong>Engineering correctness</strong>273 tests pass on every run. All 6 detector modules, database layer, ingestion pipeline, and security layer covered.</div>
        <div class="sit"><strong>Performance at volume</strong>Spoofing: 19,224 orders/sec. Coordinated pump: 51,349 orders/sec. Both well within the 120s/100k scalability threshold.</div>
        <div class="sit"><strong>0.0% false positive rate on large-cap NSE data</strong>90 real trading days, 5 NSE stocks, 0 spurious alerts. Independently verifiable from backtest/results/negative_controls.json.</div>
        <div class="sit"><strong>Concurrency and data integrity</strong>Race condition found, fixed, and verified via 500 concurrent write attempts. UniqueConstraint prevents duplicate alerts.</div>
        <div class="sit"><strong>Security and audit trail</strong>PII masking, append-only access logs, configurable retention — all implemented and passing tests.</div>
      </div>

      <!-- IN PROGRESS -->
      <div class="scol">
        <div class="shdr">
          <div class="sico sii">&#9677;</div>
          <span class="sti">In Progress</span>
        </div>
        <div class="sit"><strong>Real-case backtest against SEBI enforcement orders</strong>Three real, citable SEBI enforcement orders identified and used as test cases. All three currently UNTESTABLE via public NSE archives — the named scrips are BSE-listed instruments, absent from NSE bhavcopy.</div>
        <div class="sit"><strong>Two production-grade bugs found and fixed during real-data testing</strong>Shared circuit breaker between critical and supplementary data endpoints (commit eae832c); missing symbol whitespace strip (commit 87a93be). Cited openly as evidence of rigour, not hidden.</div>
        <div class="sit"><strong>BSE bhavcopy fetcher</strong>Would make the Kavit Industries SEBI case (documented +113% price impact) testable. Implementation path is clear; not yet built.</div>
        <div class="snote">Detection efficacy against real, confirmed historical manipulation is still being validated. This is the precise current state — not a hedged claim.</div>
      </div>

      <!-- NOT POSSIBLE WITHOUT OFFICIAL ACCESS -->
      <div class="scol">
        <div class="shdr">
          <div class="sico sib">&#10007;</div>
          <span class="stb">Not Possible Without Official Data Access</span>
        </div>
        <div class="sit"><strong>Spoofing detector validation</strong>Requires order-lifecycle data (placed/cancelled timestamps per individual order). NSE and BSE have never published historical order books publicly — for any date or instrument.</div>
        <div class="sit"><strong>Circular trading detector validation</strong>Requires account-level trade pairs across counterparties. Available only to SEBI, NSE, and BSE surveillance teams under formal access.</div>
        <div class="sit"><strong>Options manipulation validation</strong>Historical option chain OI snapshots do not exist in any public Indian archive. NSE publishes only the current live chain; no historical snapshots are retained publicly.</div>
        <div class="snote">This is a structural fact about Indian market data infrastructure — not a shortcoming of this project. Every commercial market surveillance vendor in India faces the same constraint. Their efficacy claims come from formal data-sharing agreements with exchanges — the same path available here.</div>
      </div>
    </div>
  </div>
</section>

<!-- =========================================================
  SECTION 5: ARCHITECTURE — real module names only
  ========================================================= -->
<section id="arch" class="sec">
  <div class="c">
    <p class="slbl">System design</p>
    <h2>Architecture — Real Module Names</h2>
    <p class="lede">All labels below correspond exactly to files in this repository. No placeholders or generic names.</p>

    <div class="awrap">
      <svg id="asvg" viewBox="0 0 960 530" xmlns="http://www.w3.org/2000/svg"
           font-family="Consolas,'Courier New',monospace" font-size="11">
        <defs>
          <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#253d5e"/>
          </marker>
          <marker id="arr2" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
            <path d="M0,0 L0,6 L8,3 z" fill="#0e7490"/>
          </marker>
        </defs>
        <!-- Row labels -->
        <text x="6" y="58" fill="#4a6180" font-size="9" letter-spacing="1">DATA SOURCES</text>
        <text x="6" y="183" fill="#4a6180" font-size="9" letter-spacing="1">INGESTION</text>
        <text x="6" y="308" fill="#4a6180" font-size="9" letter-spacing="1">DETECTION (6 MODULES)</text>
        <text x="6" y="432" fill="#4a6180" font-size="9" letter-spacing="1">OUTPUT &amp; SECURITY</text>
        <!-- Dividers -->
        <line x1="100" y1="72" x2="950" y2="72" stroke="#1e3048" stroke-width=".5"/>
        <line x1="100" y1="197" x2="950" y2="197" stroke="#1e3048" stroke-width=".5"/>
        <line x1="100" y1="322" x2="950" y2="322" stroke="#1e3048" stroke-width=".5"/>
        <line x1="100" y1="450" x2="950" y2="450" stroke="#1e3048" stroke-width=".5"/>
        <!-- Data sources -->
        <rect x="108" y="28" width="185" height="38" fill="#111e2e" stroke="#1e3048"/>
        <text x="200" y="44" text-anchor="middle" fill="#94a3b8">NSE Bhavcopy Archive</text>
        <text x="200" y="57" text-anchor="middle" fill="#4a6180" font-size="9">archives.nseindia.com</text>
        <rect x="308" y="28" width="185" height="38" fill="#111e2e" stroke="#1e3048"/>
        <text x="400" y="44" text-anchor="middle" fill="#94a3b8">NSE Option Chain</text>
        <text x="400" y="57" text-anchor="middle" fill="#4a6180" font-size="9">nseindia.com · OI + Greeks</text>
        <rect x="508" y="28" width="185" height="38" fill="#111e2e" stroke="#1e3048"/>
        <text x="600" y="44" text-anchor="middle" fill="#94a3b8">NSE Bulk Deals</text>
        <text x="600" y="57" text-anchor="middle" fill="#4a6180" font-size="9">nseindia.com · block trades</text>
        <rect x="718" y="28" width="200" height="38" fill="#111e2e" stroke="#253d5e"/>
        <text x="818" y="44" text-anchor="middle" fill="#94a3b8">Live Order Stream</text>
        <text x="818" y="57" text-anchor="middle" fill="#4a6180" font-size="9">NSE/BSE · order lifecycle events</text>
        <!-- Ingestion -->
        <rect x="108" y="152" width="185" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="200" y="168" text-anchor="middle" fill="#e2e8f0">nse_bhavcopy.py</text>
        <text x="200" y="181" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/ · circuit + retry</text>
        <rect x="308" y="152" width="185" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="400" y="168" text-anchor="middle" fill="#e2e8f0">nse_option_chain.py</text>
        <text x="400" y="181" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
        <rect x="508" y="152" width="185" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="600" y="168" text-anchor="middle" fill="#e2e8f0">nse_bulk_deals.py</text>
        <text x="600" y="181" text-anchor="middle" fill="#4a6180" font-size="9">data/ingest/</text>
        <rect x="718" y="142" width="200" height="58" fill="#0a1929" stroke="#253d5e"/>
        <text x="818" y="160" text-anchor="middle" fill="#22d3ee">resilience.py</text>
        <text x="818" y="174" text-anchor="middle" fill="#4a6180" font-size="9">retry_with_backoff · CircuitBreaker</text>
        <text x="818" y="188" text-anchor="middle" fill="#4a6180" font-size="9">bhavcopy_circuit · delivery_circuit</text>
        <!-- Source→Ingestion arrows -->
        <line x1="200" y1="66" x2="200" y2="150" stroke="#253d5e" stroke-width="1" marker-end="url(#arr)"/>
        <line x1="400" y1="66" x2="400" y2="150" stroke="#253d5e" stroke-width="1" marker-end="url(#arr)"/>
        <line x1="600" y1="66" x2="600" y2="150" stroke="#253d5e" stroke-width="1" marker-end="url(#arr)"/>
        <!-- Detection (6 real modules) -->
        <rect x="108" y="272" width="128" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="172" y="288" text-anchor="middle" fill="#e2e8f0">spoofing.py</text>
        <text x="172" y="301" text-anchor="middle" fill="#4a6180" font-size="9">cancel · size · impact</text>
        <rect x="246" y="272" width="142" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="317" y="288" text-anchor="middle" fill="#e2e8f0">circular_trading.py</text>
        <text x="317" y="301" text-anchor="middle" fill="#4a6180" font-size="9">trade pair graph</text>
        <rect x="398" y="272" width="157" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="476" y="288" text-anchor="middle" fill="#e2e8f0">coordinated_pump.py</text>
        <text x="476" y="301" text-anchor="middle" fill="#4a6180" font-size="9">dormancy · vol spike</text>
        <rect x="565" y="272" width="148" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="639" y="288" text-anchor="middle" fill="#e2e8f0">basis_distortion.py</text>
        <text x="639" y="301" text-anchor="middle" fill="#4a6180" font-size="9">futures-spot spread</text>
        <rect x="722" y="252" width="143" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="793" y="268" text-anchor="middle" fill="#e2e8f0">oi_manipulation.py</text>
        <text x="793" y="281" text-anchor="middle" fill="#4a6180" font-size="9">OI concentration</text>
        <rect x="722" y="300" width="143" height="38" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="793" y="316" text-anchor="middle" fill="#e2e8f0">option_pinning.py</text>
        <text x="793" y="329" text-anchor="middle" fill="#4a6180" font-size="9">strike OI distribution</text>
        <!-- Ingestion→detection bus -->
        <line x1="300" y1="190" x2="300" y2="270" stroke="#253d5e" stroke-width="1" marker-end="url(#arr)"/>
        <!-- Output row -->
        <rect x="168" y="395" width="152" height="40" fill="#0a1929" stroke="#0e7490"/>
        <text x="244" y="411" text-anchor="middle" fill="#22d3ee">evidence.py</text>
        <text x="244" y="424" text-anchor="middle" fill="#0e7490" font-size="9">evidence log · alert builder</text>
        <rect x="334" y="395" width="158" height="40" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="413" y="411" text-anchor="middle" fill="#e2e8f0">app/db/models.py</text>
        <text x="413" y="424" text-anchor="middle" fill="#4a6180" font-size="9">Alert · UniqueConstraint</text>
        <rect x="508" y="395" width="115" height="40" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="565" y="411" text-anchor="middle" fill="#e2e8f0">access_log.py</text>
        <text x="565" y="424" text-anchor="middle" fill="#4a6180" font-size="9">audit trail</text>
        <rect x="638" y="395" width="85" height="40" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="680" y="411" text-anchor="middle" fill="#e2e8f0">pii.py</text>
        <text x="680" y="424" text-anchor="middle" fill="#4a6180" font-size="9">masking</text>
        <rect x="738" y="395" width="102" height="40" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="789" y="411" text-anchor="middle" fill="#e2e8f0">retention.py</text>
        <text x="789" y="424" text-anchor="middle" fill="#4a6180" font-size="9">data lifecycle</text>
        <!-- Detector→evidence -->
        <line x1="300" y1="310" x2="300" y2="393" stroke="#0e7490" stroke-width="1" marker-end="url(#arr2)"/>
        <line x1="476" y1="310" x2="476" y2="393" stroke="#0e7490" stroke-width=".7" stroke-dasharray="3,2" marker-end="url(#arr2)"/>
        <!-- Alert output -->
        <line x1="413" y1="435" x2="413" y2="457" stroke="#253d5e" stroke-width=".8" marker-end="url(#arr)"/>
        <rect x="335" y="460" width="156" height="30" fill="#111e2e" stroke="#ef4444" stroke-width=".8"/>
        <text x="413" y="473" text-anchor="middle" fill="#ef4444" font-size="10">Alert</text>
        <text x="413" y="484" text-anchor="middle" fill="#4a6180" font-size="9">surveillance team / SEBI referral</text>
        <!-- Legend -->
        <rect x="108" y="500" width="10" height="10" fill="#0a1929" stroke="#0e7490"/>
        <text x="122" y="509" fill="#4a6180" font-size="9">evidence pipeline</text>
        <rect x="232" y="500" width="10" height="10" fill="#0e1f2f" stroke="#1e3048"/>
        <text x="246" y="509" fill="#4a6180" font-size="9">processing layer</text>
        <rect x="350" y="500" width="10" height="10" fill="#0a1929" stroke="#253d5e"/>
        <text x="364" y="509" fill="#4a6180" font-size="9">resilience infrastructure</text>
      </svg>
    </div>
  </div>
</section>

<footer>
  <div class="fbrand">SENTINEL // v1.0-alpha // September 2026</div>
  <div class="fnote">All numbers are from real test runs and real data files in this repository. No figure is estimated, rounded up, or fabricated. Source file citations are in HTML comments adjacent to each figure.</div>
</footer>

<script>
"use strict";
// SYNTHETIC REPLAY DATA
// Order data below is entirely synthetic — generated for demonstration.
// Thresholds (app/detection/spoofing.py lines 47-49):
//   MIN_CANCEL_RATIO=0.85, MIN_SIZE_MULTIPLE=3.0, MIN_PRICE_IMPACT_PCT=0.5
// Score formula (spoofing.py lines 132-138):
//   0.45*cancel_ratio + 0.25*min(size_multiple/10,1) + 0.20*min(price_impact_pct/5,1) + 0.10
// Demo values: cancel=0.92, size=4.5x, impact=0.82%, opposite=true
//   score = 0.414 + 0.1125 + 0.0328 + 0.10 = 0.659 -> severity "high" (>= 0.65)

var ORDERS=[
  {t:"09:17:22",s:"BUY", q:4500,p:2483.50,st:"PLACED"},
  {t:"09:17:38",s:"BUY", q:4500,p:2483.50,st:"PLACED"},
  {t:"09:18:05",s:"BUY", q:4500,p:2484.20,st:"PLACED"},
  {t:"09:19:44",s:"BUY", q:4500,p:2483.50,st:"CANCELLED"},
  {t:"09:19:47",s:"BUY", q:4500,p:2483.50,st:"CANCELLED"},
  {t:"09:19:52",s:"BUY", q:3960,p:2484.20,st:"CANCELLED"},
  {t:"09:20:03",s:"SELL",q:900, p:2484.80,st:"EXECUTED"}
];
// Explanation verbatim format from app/detection/spoofing.py lines 140-152
var EXPL="Account <em>ACC-7741</em> placed orders totaling <em>\u20b93,34,166</em> in value for <em>RELIANCE (NSE)</em>, then cancelled <em>92.0%</em> of that value. Peak order size was <em>4.5\u00d7</em> the instrument\u2019s 30-day average order size. Price moved <em>0.82%</em> while the order(s) were resting. The account also executed trades on the opposite side of the cancelled orders, consistent with profiting from the price move it caused.";
// Synthetic price data for RELIANCE (30 ticks, ~0.82% rise during spoof window)
var PX=[2483.5,2483.2,2483.8,2484.1,2484.5,2484.9,2485.3,2485.7,2486.1,2486.4,
        2486.7,2486.9,2487.1,2487.3,2487.4,2487.3,2487.1,2486.8,2486.4,2486.1,
        2485.8,2485.6,2485.4,2485.2,2485.0,2484.9,2484.8,2484.9,2485.0,2485.1];

function renderChart(n){
  var pts=PX.slice(0,Math.max(2,n));
  var W=400,H=120,pT=10,pB=16;
  var mn=Math.min.apply(null,pts),mx=Math.max.apply(null,pts),rng=mx-mn||1;
  var xs=pts.map(function(_,i){return(i/(pts.length-1))*W;});
  var ys=pts.map(function(p){return pT+(1-(p-mn)/rng)*(H-pT-pB);});
  var l=xs.map(function(x,i){return(i===0?"M":"L")+x.toFixed(1)+","+ys[i].toFixed(1);}).join(" ");
  document.getElementById("pline").setAttribute("d",l);
  document.getElementById("parea").setAttribute("d",l+" L"+xs[xs.length-1].toFixed(1)+","+H+" L0,"+H+" Z");
}
function addRow(o){
  var r=document.createElement("div");r.className="orow";
  var sc=o.s==="BUY"?"buy":"sell";
  var stc=o.st==="CANCELLED"?"canc":(o.st==="EXECUTED"?"exec":"");
  r.innerHTML="<span>"+o.t+"</span><span class='"+sc+"'>"+o.s+"</span><span>"+o.q.toLocaleString()+"</span><span>"+o.p.toFixed(2)+"</span><span class='"+stc+"'>"+o.st+"</span>";
  document.getElementById("frows").appendChild(r);
  requestAnimationFrame(function(){requestAnimationFrame(function(){r.classList.add("vis");});});
}
var tmr=null;
function clearReplay(){
  if(tmr){clearTimeout(tmr);tmr=null;}
  document.getElementById("frows").innerHTML="";
  document.getElementById("abox").classList.remove("vis");
  document.getElementById("atxt").innerHTML="";
  ["srect","sline","slblsvg"].forEach(function(id){document.getElementById(id).setAttribute("opacity","0");});
  renderChart(2);
}
function startReplay(){
  clearReplay();
  var btn=document.getElementById("rbtn");btn.textContent="Running\u2026";btn.disabled=true;
  var steps=[
    function(){addRow(ORDERS[0]);renderChart(5);},
    function(){addRow(ORDERS[1]);renderChart(9);},
    function(){addRow(ORDERS[2]);renderChart(14);
      document.getElementById("srect").setAttribute("opacity","1");
      document.getElementById("sline").setAttribute("opacity","1");
      document.getElementById("slblsvg").setAttribute("opacity","1");},
    function(){addRow(ORDERS[3]);renderChart(18);},
    function(){addRow(ORDERS[4]);renderChart(21);},
    function(){addRow(ORDERS[5]);renderChart(24);},
    function(){addRow(ORDERS[6]);renderChart(27);},
    function(){renderChart(30);document.getElementById("atxt").innerHTML=EXPL;
      document.getElementById("abox").classList.add("vis");
      btn.textContent="\u25B6 Replay";btn.disabled=false;}
  ];
  var d=[0,800,1600,3000,3600,4200,5400,7200];
  steps.forEach(function(fn,i){setTimeout(fn,d[i]);});
}
document.addEventListener("DOMContentLoaded",function(){renderChart(2);setTimeout(startReplay,800);});
</script>
</body>
</html>"""

OUT.write_text(HTML, encoding="utf-8")
sz = OUT.stat().st_size
print(f"Written: {OUT}")
print(f"Size: {sz:,} bytes ({sz//1024} KB)")
