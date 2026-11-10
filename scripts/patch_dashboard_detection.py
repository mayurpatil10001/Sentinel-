"""
scripts/patch_dashboard_detection.py
======================================
Patches dashboard/static/index.html to wire up real detection pipeline:

  1. Add "▶ Run Live Scan" button in header command bar
  2. Add live scan status bar below telemetry ticker
  3. Update syncAllData() to also fetch heatmap + trigger scan
  4. Update renderCommandCenter() heatmap to use real anomaly scores
  5. Add live scan results panel in Command Center tab
  6. Auto-trigger scan on page load

Run: python scripts/patch_dashboard_detection.py
"""

import pathlib, sys, re

HTML_PATH = pathlib.Path(__file__).parent.parent / "dashboard" / "static" / "index.html"

src = HTML_PATH.read_text(encoding="utf-8", errors="surrogateescape")
orig_len = len(src)

patches = []

# ─── PATCH 1: Add "Run Live Scan" button next to Refresh ─────────────────────
OLD1 = '''<button class="btn" onclick="syncAllData()" title="Re-query API Feeds">↻ Refresh</button>'''
NEW1 = '''<button class="btn" onclick="syncAllData()" title="Re-query API Feeds">↻ Refresh</button>
        <button class="btn" id="scanBtn" onclick="runLiveScan()" title="Run manipulation detection on live market data" style="background:var(--crimson);color:#fff;font-weight:700;">▶ Run Live Scan</button>'''
patches.append(("Add Run Live Scan button", OLD1, NEW1))

# ─── PATCH 2: Add scan status bar after telemetry ticker opening ─────────────
OLD2 = '''<div>FYERS LIVE: <b id="fyersBadge"'''
NEW2 = '''<div id="scanStatusBar" style="display:none;background:#1a1f2e;border:1px solid var(--crimson);border-radius:4px;padding:3px 10px;font-size:11px;color:#f85149;">
        ⚡ <span id="scanStatusText">Scanning…</span>
      </div>
      <div>FYERS LIVE: <b id="fyersBadge"'''
patches.append(("Add scan status bar", OLD2, NEW2))

# ─── PATCH 3: Add live scan results panel inside Command Center ───────────────
OLD3 = '''<!-- Real-Time Anomaly Heatmap (Scrips) -->'''
NEW3 = '''<!-- Live Scan Results Panel -->
          <div class="card" id="liveScanCard" style="margin-bottom:16px;">
            <div class="card-head" style="display:flex;justify-content:space-between;align-items:center;">
              <div class="card-head-title">⚡ Live Detection Results</div>
              <div style="display:flex;gap:8px;align-items:center;">
                <span id="scanTimestamp" style="font-size:10px;color:var(--text-dim);font-family:var(--font-mono);">Not scanned yet</span>
                <button class="btn" onclick="runLiveScan()" style="font-size:11px;padding:4px 10px;">▶ Scan Now</button>
              </div>
            </div>
            <div id="liveScanResults" style="padding:12px;">
              <div style="color:var(--text-dim);text-align:center;padding:20px;font-size:12px;">
                Click <b>▶ Run Live Scan</b> to run real manipulation detection on live Fyers market data.<br>
                Detectors: Volume Spike · Price Z-Score · Thin Volume Pump · Momentum Reversal · OI Concentration
              </div>
            </div>
          </div>

          <!-- Real-Time Anomaly Heatmap (Scrips) -->'''
patches.append(("Add live scan results panel", OLD3, NEW3))

# ─── PATCH 4: Replace syncAllData() to also pull heatmap ─────────────────────
OLD4 = '''// Data Fetching
async function syncAllData() {
  try {
    const [insts, alertsRes, detRes] = await Promise.all([
      api('/instruments'),
      api('/alerts?page=1&page_size=150'),
      api('/system/detector-status')
    ]);

    state.instruments = insts;
    state.alerts = alertsRes.items || [];
    state.detectors = detRes.detectors || [];

    renderCommandCenter();
    renderWatchlistTable();
    renderAlertsQueueTable();
    renderInstrumentsTable();
    renderDetectorsGrid();
  } catch (err) {
    console.error('Sync failed:', err);
  }
}'''
NEW4 = '''// Data Fetching
async function syncAllData() {
  try {
    const [insts, alertsRes, detRes, hmRes] = await Promise.all([
      api('/instruments'),
      api('/alerts?page=1&page_size=150'),
      api('/system/detector-status'),
      fetch('/api/live/heatmap').then(r=>r.json()).catch(()=>({instruments:[]})),
    ]);

    state.instruments = insts;
    state.alerts = alertsRes.items || [];
    state.detectors = detRes.detectors || [];
    state.heatmap = hmRes.instruments || [];

    renderCommandCenter();
    renderWatchlistTable();
    renderAlertsQueueTable();
    renderInstrumentsTable();
    renderDetectorsGrid();
  } catch (err) {
    console.error('Sync failed:', err);
  }
}

// Live Scan Runner
async function runLiveScan() {
  const btn = document.getElementById('scanBtn');
  const bar = document.getElementById('scanStatusBar');
  const txt = document.getElementById('scanStatusText');
  const res = document.getElementById('liveScanResults');
  const ts  = document.getElementById('scanTimestamp');

  if (btn) { btn.disabled = true; btn.textContent = '⏳ Scanning…'; }
  if (bar) bar.style.display = 'block';
  if (txt) txt.textContent = 'Scanning market data…';

  try {
    const r = await fetch('/api/live/scan?max_symbols=15');
    const d = await r.json();

    const scanned = d.symbols_scanned || 0;
    const found   = (d.detections || []).length;
    const created = d.alerts_generated || 0;

    if (ts) ts.textContent = new Date().toLocaleTimeString('en-IN') + ' · ' + scanned + ' symbols scanned';
    if (txt) txt.textContent = `✓ ${scanned} symbols scanned · ${found} signals · ${created} new alerts`;

    renderScanResults(d.detections || []);

    // Refresh alert queue with real alerts
    try {
      const aRes = await api('/alerts?page=1&page_size=150');
      state.alerts = aRes.items || [];
      renderAlertsQueueTable();
      renderCommandCenter();
    } catch(e) {}

    // Refresh heatmap
    try {
      const hm = await fetch('/api/live/heatmap').then(r=>r.json());
      state.heatmap = hm.instruments || [];
      renderHeatmapReal();
    } catch(e) {}

  } catch(e) {
    if (txt) txt.textContent = '✗ Scan failed: ' + e.message;
    console.error('Live scan failed:', e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '▶ Run Live Scan'; }
    setTimeout(() => { if (bar) bar.style.display = 'none'; }, 8000);
  }
}

function renderScanResults(detections) {
  const el = document.getElementById('liveScanResults');
  if (!el) return;
  if (!detections.length) {
    el.innerHTML = '<div style="color:var(--text-dim);text-align:center;padding:20px;font-size:12px;">✓ No anomalies detected in scanned symbols. Market appears normal.</div>';
    return;
  }
  el.innerHTML = detections.map(d => {
    const sevColor = {critical:'#f85149',high:'#ff7b72',medium:'#e3b341',low:'#3fb950'}[d.severity] || '#8b949e';
    const patLabel = (d.pattern||'').replace(/_/g,' ').toUpperCase();
    return `<div style="background:var(--bg-card);border:1px solid ${sevColor}33;border-left:3px solid ${sevColor};border-radius:5px;padding:10px 14px;margin-bottom:8px;">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
        <span style="font-weight:700;font-size:13px;color:#fff;font-family:var(--font-display);">${esc(d.symbol)}</span>
        <span style="display:flex;gap:6px;align-items:center;">
          <span style="font-size:10px;font-family:var(--font-mono);background:${sevColor}22;color:${sevColor};padding:2px 6px;border-radius:3px;">${d.severity?.toUpperCase()}</span>
          <span style="font-size:11px;color:var(--text-dim);">score: <b style="color:#fff;">${(d.score||0).toFixed(3)}</b></span>
        </span>
      </div>
      <div style="font-size:10px;color:${sevColor};font-family:var(--font-mono);margin-bottom:4px;">${patLabel}</div>
      <div style="font-size:11px;color:var(--text-dim);line-height:1.5;">${esc(d.explanation_short||'')}</div>
      ${d.new_alert ? '<div style="font-size:10px;color:var(--emerald);margin-top:4px;">✓ New alert created in queue</div>' : '<div style="font-size:10px;color:var(--text-muted);margin-top:4px;">— Duplicate: already in queue</div>'}
    </div>`;
  }).join('');
}

function renderHeatmapReal() {
  const heatCont = document.getElementById('heatmapGrid');
  if (!heatCont) return;
  const data = state.heatmap || [];
  if (!data.length) return;

  // Show top 24 by anomaly score
  const top = data.slice(0, 24);
  heatCont.innerHTML = top.map(item => {
    const score = item.anomaly_score || 0;
    const isCrit   = score >= 0.8;
    const isHigh   = score >= 0.6;
    const isMed    = score >= 0.4;
    const chipClass = isCrit ? 'hot' : (isHigh ? 'warm' : '');
    const scoreBar  = Math.round(score * 100);
    const sevColor  = isCrit ? '#f85149' : (isHigh ? '#ff7b72' : (isMed ? '#e3b341' : '#3fb950'));
    return `<div class="heat-chip ${chipClass}" style="border-color:${score>0?sevColor+'44':'var(--border)'};">
      <div class="heat-symbol">${esc(item.symbol)}</div>
      <div class="heat-seg">${esc(item.instrument_type)} · ${esc(item.exchange)}</div>
      <div class="heat-metric">
        <span style="color:var(--text-dim);">Score: <b style="color:${sevColor};">${score.toFixed(2)}</b></span>
        ${item.pattern ? '<span style="font-size:9px;color:'+sevColor+';">'+esc(item.pattern.replace(/_/g,' '))+'</span>' : '<span style="font-size:9px;color:var(--text-dim);">CLEAN</span>'}
      </div>
      <div style="height:3px;background:#2d333b;border-radius:2px;margin-top:4px;"><div style="height:3px;width:${scoreBar}%;background:${sevColor};border-radius:2px;transition:width .5s;"></div></div>
    </div>`;
  }).join('');
}'''
patches.append(("Wire syncAllData + runLiveScan", OLD4, NEW4))

# ─── PATCH 5: Update renderCommandCenter heatmap to prefer real data ──────────
OLD5 = '''  // Heatmap rendering
  const heatCont = document.getElementById('heatmapGrid');
  const top12 = state.instruments.slice(0, 12);
  heatCont.innerHTML = top12.map(i => {
    const hasAlert = i.open_alert_count > 0;
    const isCrit = i.last_alert_severity === 'critical' || i.last_alert_severity === 'high';
    return `
      <div class="heat-chip ${isCrit ? 'hot' : ''}" onclick="openScripDossier('${esc(i.id)}')">
        <div class="heat-symbol">${esc(i.symbol)}</div>
        <div class="heat-seg">${esc(i.instrument_type)} · ${esc(i.exchange)}</div>
        <div class="heat-metric">
          <span style="color:var(--text-dim);">Alerts: <b style="color:${hasAlert ? 'var(--crimson)' : 'var(--emerald)'};">${i.open_alert_count || 0}</b></span>
          <span>${i.last_alert_severity ? sevPill(i.last_alert_severity) : '<span class="tag tag-clean">CLEAN</span>'}</span>
        </div>
      </div>
    `;
  }).join('');'''
NEW5 = '''  // Heatmap rendering — prefer real anomaly scores from /api/live/heatmap
  if (state.heatmap && state.heatmap.length) {
    renderHeatmapReal();
  } else {
    const heatCont = document.getElementById('heatmapGrid');
    const top12 = state.instruments.slice(0, 12);
    heatCont.innerHTML = top12.map(i => {
      const hasAlert = i.open_alert_count > 0;
      const isCrit = i.last_alert_severity === 'critical' || i.last_alert_severity === 'high';
      return `
        <div class="heat-chip ${isCrit ? 'hot' : ''}" onclick="openScripDossier('${esc(i.id)}')">
          <div class="heat-symbol">${esc(i.symbol)}</div>
          <div class="heat-seg">${esc(i.instrument_type)} · ${esc(i.exchange)}</div>
          <div class="heat-metric">
            <span style="color:var(--text-dim);">Alerts: <b style="color:${hasAlert ? 'var(--crimson)' : 'var(--emerald)'};">${i.open_alert_count || 0}</b></span>
            <span>${i.last_alert_severity ? sevPill(i.last_alert_severity) : '<span class="tag tag-clean">CLEAN</span>'}</span>
          </div>
        </div>
      `;
    }).join('');
  }'''
patches.append(("Heatmap uses real anomaly scores", OLD5, NEW5))

# ─── PATCH 6: Init state.heatmap and trigger auto-scan hint on load ───────────
OLD6 = '''document.addEventListener('DOMContentLoaded',()=>{
  fyersPollStatus();
  setInterval(fyersPollStatus,30000);
  setInterval(fyersRefreshPrices,15000);
});'''
NEW6 = '''document.addEventListener('DOMContentLoaded',()=>{
  if (!state.heatmap) state.heatmap = [];
  fyersPollStatus();
  setInterval(fyersPollStatus,30000);
  setInterval(fyersRefreshPrices,15000);
  // Auto-refresh scan status after 2s to show last results
  setTimeout(async()=>{
    try {
      const ss = await fetch('/api/live/scan/status').then(r=>r.json());
      if (ss.last_scan) {
        const ts = document.getElementById('scanTimestamp');
        if (ts) ts.textContent = 'Last scan: ' + new Date(ss.last_scan).toLocaleTimeString('en-IN');
        if (ss.recent_detections && ss.recent_detections.length)
          renderScanResults(ss.recent_detections);
      }
      // Load heatmap data
      const hm = await fetch('/api/live/heatmap').then(r=>r.json());
      state.heatmap = hm.instruments || [];
      renderHeatmapReal();
    } catch(e) {}
  }, 2000);
});'''
patches.append(("Auto-load heatmap on DOMContentLoaded", OLD6, NEW6))

# ─── Apply all patches ────────────────────────────────────────────────────────
applied = 0
failed  = []
for name, old, new in patches:
    if old in src:
        src = src.replace(old, new, 1)
        print(f"  ✅ {name}")
        applied += 1
    else:
        print(f"  ⚠️  SKIP (not found): {name}")
        failed.append(name)

HTML_PATH.write_text(src, encoding="utf-8", errors="surrogateescape")
print(f"\nPatched {applied}/{len(patches)} sections. File: {len(src):,} bytes (was {orig_len:,})")
if failed:
    print("FAILED patches:", failed)
