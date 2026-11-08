"""
Patch dashboard/static/index.html to add Fyers live data wiring.
Run from d:\Sentinel: python dashboard/scripts/apply_fyers_patches.py
"""
import pathlib

DASHBOARD = pathlib.Path("dashboard/static/index.html")
html = DASHBOARD.read_text(encoding="utf-8")
orig = len(html)
patches = 0

# ── 1. Add Fyers badge in telemetry ticker ──────────────────────────────────
TICKER_OLD = "CIRCUIT BREAKER: <b class=\"green\">NSE BHAVCOPY CLOSED (OPERATIONAL)</b></div>"
TICKER_NEW = (
    "CIRCUIT BREAKER: <b class=\"green\">NSE BHAVCOPY CLOSED (OPERATIONAL)</b></div>\n"
    "      <div>FYERS API: <b id=\"fyersBadge\" class=\"amber\" style=\"cursor:pointer\" "
    "onclick=\"document.getElementById('fyersAuthModal').style.display='flex'\">NOT CONNECTED</b></div>"
)
if TICKER_OLD in html and "fyersBadge" not in html:
    html = html.replace(TICKER_OLD, TICKER_NEW)
    patches += 1
    print("✅ Patched: Fyers badge added to telemetry ticker")
else:
    print("SKIP: ticker badge (already patched or not found)")

# ── 2. Add auth modal + Fyers JS before </body> ─────────────────────────────
FYERS_INJECT = """
<!-- ════════════════════════════════════════════════════════════════
     FYERS LIVE DATA — AUTH MODAL
════════════════════════════════════════════════════════════════ -->
<div id="fyersAuthModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:32px;max-width:540px;width:90%;position:relative;">
    <button onclick="document.getElementById('fyersAuthModal').style.display='none'"
      style="position:absolute;top:12px;right:16px;background:none;border:none;color:#8b949e;font-size:18px;cursor:pointer;">&#x2715;</button>
    <h3 style="color:#58a6ff;margin:0 0 8px;">&#128279; Connect Fyers Live Data</h3>
    <p style="color:#8b949e;font-size:13px;margin:0 0 20px;">
      Authenticate with Fyers to stream real-time quotes, market depth, and option chain
      data into the Sentinel surveillance terminal. Token refreshes daily.
    </p>
    <p style="color:#e6edf3;font-size:13px;margin:0 0 12px;">
      <b>Step 1:</b> Generate a secure login URL and open it in your browser.
    </p>
    <button class="btn" id="fyersGetUrlBtn" onclick="fyersGetAuthUrl()" style="width:100%;margin-bottom:12px;">
      &#128273; Generate Fyers Login URL
    </button>
    <div id="fyersAuthUrlBox" style="display:none;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:12px;margin-bottom:16px;">
      <p style="color:#8b949e;font-size:12px;margin:0 0 6px;">Open this URL in your browser:</p>
      <a id="fyersAuthUrl" href="#" target="_blank" style="color:#58a6ff;font-size:11px;word-break:break-all;"></a>
    </div>
    <p style="color:#e6edf3;font-size:13px;margin:0 0 8px;">
      <b>Step 2:</b> After Fyers login &amp; redirect, paste the full redirect URL or just the auth_code:
    </p>
    <input id="fyersCodeInput" type="text" placeholder="Paste redirect URL or auth_code here..."
      style="width:100%;box-sizing:border-box;background:#161b22;border:1px solid #30363d;border-radius:4px;color:#e6edf3;padding:8px 10px;font-size:13px;margin-bottom:12px;">
    <button class="btn" onclick="fyersSubmitToken()" style="width:100%;background:#238636;">
      &#10003; Activate Live Data
    </button>
    <div id="fyersAuthStatus" style="margin-top:12px;font-size:13px;min-height:24px;"></div>
  </div>
</div>

<script>
// ════════════════════════════════════════════════════════════════
//  FYERS LIVE DATA INTEGRATION  — Sentinel dashboard wiring
// ════════════════════════════════════════════════════════════════

const fyersLiveQuotes = {};   // keyed by Fyers symbol string

function fyersSym2Base(sym) {
  const idx = {'NSE:NIFTY50-INDEX':'NIFTY 50','NSE:NIFTYBANK-INDEX':'NIFTY BANK'};
  if (idx[sym]) return idx[sym];
  return sym.replace(/^(NSE|BSE):/, '').replace(/-EQ$/, '');
}

// ── Status badge ────────────────────────────────────────────────
async function fyersPollStatus() {
  try {
    const r = await fetch('/api/live/status');
    if (!r.ok) throw new Error(r.status);
    const d = await r.json();
    const badge = document.getElementById('fyersBadge');
    if (!badge) return;
    if (d.token_valid) {
      badge.textContent = 'FYERS CONNECTED (' + d.app_id + ')';
      badge.className = 'green';
      fyersRefreshPrices();
    } else {
      badge.textContent = 'NOT CONNECTED — click to authenticate';
      badge.className = 'amber';
    }
  } catch(e) {
    const b = document.getElementById('fyersBadge');
    if (b) { b.textContent = 'API UNREACHABLE'; b.className = 'red'; }
  }
}

// ── Live price refresh ──────────────────────────────────────────
async function fyersRefreshPrices() {
  try {
    const r = await fetch('/api/live/watchlist');
    if (!r.ok) return;
    const d = await r.json();
    if (d.status !== 'ok') return;
    let totalVol = 0;
    for (const q of (d.quotes || [])) {
      fyersLiveQuotes[q.symbol] = q;
      fyersLiveQuotes[fyersSym2Base(q.symbol)] = q;
      totalVol += (q.volume || 0);
    }
    // Update ingest ticker with real volume
    const el = document.getElementById('tickerIngest');
    if (el) el.textContent = totalVol.toLocaleString('en-IN') + ' vol (Fyers live)';
    // Update any cells tagged data-live-symbol
    document.querySelectorAll('[data-live-symbol]').forEach(cell => {
      const sym = cell.dataset.liveSymbol;
      const field = cell.dataset.liveField || 'ltp';
      const q = fyersLiveQuotes[sym];
      if (!q || q[field] == null) return;
      const v = q[field];
      if (field === 'ltp') {
        cell.textContent = '&#8377;' + Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
      } else if (field === 'change_pct') {
        cell.textContent = (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%';
        cell.style.color = v >= 0 ? '#3fb950' : '#f85149';
      }
      cell.style.transition = 'background .3s';
      cell.style.background = '#1a3028';
      setTimeout(() => { cell.style.background = ''; }, 700);
    });
  } catch(e) { console.warn('Fyers price refresh failed', e); }
}

// ── Auth Step 1 — get login URL ──────────────────────────────────
async function fyersGetAuthUrl() {
  const btn = document.getElementById('fyersGetUrlBtn');
  btn.textContent = '&#9203; Generating URL...'; btn.disabled = true;
  try {
    const r = await fetch('/api/live/authenticate', {method:'POST'});
    const d = await r.json();
    document.getElementById('fyersAuthUrl').href = d.auth_url;
    document.getElementById('fyersAuthUrl').textContent = d.auth_url;
    document.getElementById('fyersAuthUrlBox').style.display = 'block';
  } catch(e) {
    document.getElementById('fyersAuthStatus').innerHTML = '<span style="color:#f85149">&#10060; Failed to generate URL: ' + e.message + '</span>';
  } finally {
    btn.textContent = '&#128273; Generate Fyers Login URL'; btn.disabled = false;
  }
}

// ── Auth Step 2 — exchange code for token ────────────────────────
async function fyersSubmitToken() {
  const raw = document.getElementById('fyersCodeInput').value.trim();
  const statusEl = document.getElementById('fyersAuthStatus');
  if (!raw) { statusEl.innerHTML = '<span style="color:#f85149">Please paste the redirect URL or auth_code first.</span>'; return; }
  let code = raw;
  try { const u = new URL(raw); code = u.searchParams.get('auth_code') || u.searchParams.get('code') || raw; } catch {}
  statusEl.innerHTML = '<span style="color:#8b949e">&#9203; Connecting to Fyers...</span>';
  try {
    const r = await fetch('/api/live/token', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({auth_code: code})
    });
    const d = await r.json();
    if (r.ok && d.status === 'ok') {
      statusEl.innerHTML = '<span style="color:#3fb950">&#10003; ' + d.message + '</span>';
      setTimeout(() => { document.getElementById('fyersAuthModal').style.display='none'; fyersPollStatus(); }, 2000);
    } else {
      statusEl.innerHTML = '<span style="color:#f85149">&#10060; ' + (d.detail || JSON.stringify(d)) + '</span>';
    }
  } catch(e) {
    statusEl.innerHTML = '<span style="color:#f85149">&#10060; Network error: ' + e.message + '</span>';
  }
}

// ── Boot ─────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  fyersPollStatus();
  setInterval(fyersPollStatus, 30000);
  setInterval(fyersRefreshPrices, 15000);
});
</script>
"""

if "</body>" in html and "fyersAuthModal" not in html:
    html = html.replace("</body>", FYERS_INJECT + "\n</body>")
    patches += 1
    print("✅ Patched: auth modal + Fyers JS injected")
else:
    print("SKIP: auth modal (already patched or </body> not found)")

DASHBOARD.write_text(html, encoding="utf-8")
print(f"\nDone: {orig:,} → {len(html):,} bytes  |  {patches} patches applied")

