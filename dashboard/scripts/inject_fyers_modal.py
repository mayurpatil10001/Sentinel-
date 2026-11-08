"""
Inject Fyers auth modal + JS into the dashboard (idempotent).
Run: python dashboard/scripts/inject_fyers_modal.py
"""
import pathlib

DASHBOARD = pathlib.Path("dashboard/static/index.html")
html = DASHBOARD.read_text(encoding="utf-8")

# Guard — only inject if the modal <div> is not already present
if 'id="fyersAuthModal"' in html:
    print("SKIP: Fyers modal already in dashboard.")
else:
    INJECT = """
<!-- ══════════════════════════════════════════════════════════════
     FYERS LIVE DATA AUTH MODAL + JS
══════════════════════════════════════════════════════════════ -->
<div id="fyersAuthModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:32px;max-width:540px;width:90%;position:relative;">
    <button onclick="document.getElementById('fyersAuthModal').style.display='none'"
      style="position:absolute;top:12px;right:16px;background:none;border:none;color:#8b949e;font-size:20px;cursor:pointer;">&#x2715;</button>
    <h3 style="color:#58a6ff;margin:0 0 8px;">&#128279; Connect Fyers Live Data</h3>
    <p style="color:#8b949e;font-size:13px;margin:0 0 20px;">
      Authenticate with your Fyers account (App ID: S08D2750W7-100) to stream
      real-time quotes, market depth, and option chain data into Sentinel.
      Token must be refreshed once per trading day.
    </p>
    <p style="color:#e6edf3;font-size:13px;margin:0 0 10px;"><b>Step 1:</b> Generate a Fyers login URL.</p>
    <button class="btn" id="fyersGetUrlBtn" onclick="fyersGetAuthUrl()" style="width:100%;margin-bottom:12px;">
      &#128273; Generate Fyers Login URL
    </button>
    <div id="fyersAuthUrlBox" style="display:none;background:#161b22;border:1px solid #30363d;border-radius:4px;padding:12px;margin-bottom:14px;">
      <p style="color:#8b949e;font-size:12px;margin:0 0 6px;">Open this URL in your browser and login:</p>
      <a id="fyersAuthUrl" href="#" target="_blank" style="color:#58a6ff;font-size:11px;word-break:break-all;"></a>
    </div>
    <p style="color:#e6edf3;font-size:13px;margin:0 0 8px;">
      <b>Step 2:</b> After redirect, paste the full redirect URL or just the auth_code:
    </p>
    <input id="fyersCodeInput" type="text" placeholder="Paste redirect URL or auth_code..."
      style="width:100%;box-sizing:border-box;background:#161b22;border:1px solid #30363d;border-radius:4px;color:#e6edf3;padding:9px 10px;font-size:13px;margin-bottom:12px;">
    <button class="btn" onclick="fyersSubmitToken()" style="width:100%;background:#238636;border-color:#238636;">
      &#10003; Activate Live Data
    </button>
    <div id="fyersAuthStatus" style="margin-top:12px;font-size:13px;min-height:22px;"></div>
  </div>
</div>

<script>
// ════════ FYERS LIVE DATA — Sentinel Integration ════════
const _fyersQ = {};
function _fSym(s) {
  if (s==='NSE:NIFTY50-INDEX') return 'NIFTY 50';
  if (s==='NSE:NIFTYBANK-INDEX') return 'NIFTY BANK';
  return s.replace(/^(NSE|BSE):/,'').replace(/-EQ$/,'');
}
async function fyersPollStatus() {
  try {
    const r = await fetch('/api/live/status');
    const d = await r.json();
    const b = document.getElementById('fyersBadge');
    if (!b) return;
    if (d.token_valid) {
      b.textContent = 'FYERS CONNECTED (' + d.app_id + ')';
      b.className = 'green';
      fyersRefreshPrices();
    } else {
      b.textContent = 'NOT CONNECTED \u2014 click to authenticate';
      b.className = 'amber';
    }
  } catch(e) {
    const b = document.getElementById('fyersBadge');
    if (b) { b.textContent = 'API UNREACHABLE'; b.className = 'red'; }
  }
}
async function fyersRefreshPrices() {
  try {
    const r = await fetch('/api/live/watchlist'); if (!r.ok) return;
    const d = await r.json(); if (d.status!=='ok') return;
    let vol = 0;
    for (const q of (d.quotes||[])) {
      _fyersQ[q.symbol] = q; _fyersQ[_fSym(q.symbol)] = q; vol += (q.volume||0);
    }
    const el = document.getElementById('tickerIngest');
    if (el) el.textContent = vol.toLocaleString('en-IN') + ' vol (Fyers live)';
    document.querySelectorAll('[data-live-symbol]').forEach(c => {
      const q = _fyersQ[c.dataset.liveSymbol]; if (!q) return;
      const f = c.dataset.liveField||'ltp', v = q[f]; if (v==null) return;
      if (f==='ltp') c.textContent = '\u20b9' + Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
      else if (f==='change_pct') { c.textContent=(v>=0?'+':'')+Number(v).toFixed(2)+'%'; c.style.color=v>=0?'#3fb950':'#f85149'; }
      c.style.transition='background .3s'; c.style.background='#1a3028';
      setTimeout(()=>{c.style.background='';},700);
    });
  } catch(e){ console.warn('Fyers price refresh failed',e); }
}
async function fyersGetAuthUrl() {
  const btn = document.getElementById('fyersGetUrlBtn');
  btn.textContent = 'Generating...'; btn.disabled = true;
  try {
    const r = await fetch('/api/live/authenticate',{method:'POST'});
    const d = await r.json();
    document.getElementById('fyersAuthUrl').href = d.auth_url;
    document.getElementById('fyersAuthUrl').textContent = d.auth_url;
    document.getElementById('fyersAuthUrlBox').style.display = 'block';
  } catch(e) {
    document.getElementById('fyersAuthStatus').innerHTML='<span style="color:#f85149">Failed: '+e.message+'</span>';
  } finally { btn.textContent='\uD83D\uDD13 Generate Fyers Login URL'; btn.disabled=false; }
}
async function fyersSubmitToken() {
  const raw = document.getElementById('fyersCodeInput').value.trim();
  const st = document.getElementById('fyersAuthStatus');
  if (!raw) { st.innerHTML='<span style="color:#f85149">Paste the redirect URL or auth_code first.</span>'; return; }
  let code = raw;
  try { const u=new URL(raw); code=u.searchParams.get('auth_code')||u.searchParams.get('code')||raw; } catch{}
  st.innerHTML='<span style="color:#8b949e">Connecting...</span>';
  try {
    const r = await fetch('/api/live/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({auth_code:code})});
    const d = await r.json();
    if (r.ok && d.status==='ok') {
      st.innerHTML='<span style="color:#3fb950">\u2705 '+d.message+'</span>';
      setTimeout(()=>{document.getElementById('fyersAuthModal').style.display='none';fyersPollStatus();},2000);
    } else {
      st.innerHTML='<span style="color:#f85149">\u274C '+(d.detail||JSON.stringify(d))+'</span>';
    }
  } catch(e){ st.innerHTML='<span style="color:#f85149">\u274C '+e.message+'</span>'; }
}
document.addEventListener('DOMContentLoaded',()=>{
  fyersPollStatus();
  setInterval(fyersPollStatus,30000);
  setInterval(fyersRefreshPrices,15000);
});
</script>
"""
    html = html.replace("</body>", INJECT + "\n</body>")
    DASHBOARD.write_text(html, encoding="utf-8")
    print(f"✅ Injected Fyers modal + JS. File size: {len(html):,} bytes")

