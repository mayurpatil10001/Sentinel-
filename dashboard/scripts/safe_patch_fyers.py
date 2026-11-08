"""
Safe Fyers live-data patcher for dashboard/static/index.html.
Reads as bytes → decodes with surrogateescape → patches → encodes back.
Run from d:\Sentinel: python dashboard/scripts/safe_patch_fyers.py
"""
import pathlib

DASHBOARD = pathlib.Path("dashboard/static/index.html")

# Read as raw bytes and decode with surrogateescape so no data is lost
raw = DASHBOARD.read_bytes()
html = raw.decode("utf-8", errors="surrogateescape")
orig = len(html)
patches = 0

# ══ PATCH 1: Fyers badge in telemetry ticker ══════════════════════════════
OLD_CB = 'CIRCUIT BREAKER: <b class="green">NSE BHAVCOPY CLOSED (OPERATIONAL)</b></div>'
NEW_CB = (
    'CIRCUIT BREAKER: <b class="green">NSE BHAVCOPY CLOSED (OPERATIONAL)</b></div>\n'
    '      <div>FYERS API: <b id="fyersBadge" class="amber" style="cursor:pointer" '
    "onclick=\"document.getElementById('fyersAuthModal').style.display='flex'\">"
    "NOT CONNECTED</b></div>"
)
if OLD_CB in html and "fyersBadge" not in html:
    html = html.replace(OLD_CB, NEW_CB)
    patches += 1
    print("PATCH 1 OK: Fyers badge added to telemetry ticker")
else:
    print("PATCH 1 SKIP: ticker badge (already present or marker not found)")

# ══ PATCH 2: Auth modal + Fyers JS before </body> ════════════════════════
FYERS_BLOCK = r"""
<!-- ═══════════════════════════════════════ FYERS LIVE DATA ═══════ -->
<div id="fyersAuthModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,0.78);z-index:9999;align-items:center;justify-content:center;">
  <div style="background:#0d1117;border:1px solid #30363d;border-radius:8px;padding:32px;max-width:540px;width:90%;position:relative;">
    <button onclick="document.getElementById('fyersAuthModal').style.display='none'"
      style="position:absolute;top:12px;right:16px;background:none;border:none;color:#8b949e;font-size:20px;cursor:pointer;">x</button>
    <h3 style="color:#58a6ff;margin:0 0 8px;">Connect Fyers Live Data</h3>
    <p style="color:#8b949e;font-size:13px;margin:0 0 20px;">
      Authenticate with Fyers (App: S08D2750W7-100) to stream real-time quotes,
      market depth, and option chain data. Token must be refreshed once per trading day.
    </p>
    <p style="color:#e6edf3;font-size:13px;margin:0 0 10px;"><b>Step 1:</b> Generate a Fyers login URL.</p>
    <button class="btn" id="fyersGetUrlBtn" onclick="fyersGetAuthUrl()" style="width:100%;margin-bottom:12px;">
      Generate Fyers Login URL
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
      Activate Live Data
    </button>
    <div id="fyersAuthStatus" style="margin-top:12px;font-size:13px;min-height:22px;"></div>
  </div>
</div>
<script>
/* ══ FYERS LIVE DATA — Sentinel Integration ══ */
const _fyQ={};
function _fS(s){if(s==='NSE:NIFTY50-INDEX')return 'NIFTY 50';if(s==='NSE:NIFTYBANK-INDEX')return 'NIFTY BANK';return s.replace(/^(NSE|BSE):/,'').replace(/-EQ$/,'');}
async function fyersPollStatus(){
  try{
    const r=await fetch('/api/live/status'),d=await r.json();
    const b=document.getElementById('fyersBadge');if(!b)return;
    if(d.token_valid){b.textContent='FYERS CONNECTED ('+d.app_id+')';b.className='green';fyersRefreshPrices();}
    else{b.textContent='NOT CONNECTED - click to authenticate';b.className='amber';}
  }catch(e){const b=document.getElementById('fyersBadge');if(b){b.textContent='API UNREACHABLE';b.className='red';}}
}
async function fyersRefreshPrices(){
  try{
    const r=await fetch('/api/live/watchlist');if(!r.ok)return;
    const d=await r.json();if(d.status!=='ok')return;
    let vol=0;
    for(const q of(d.quotes||[])){_fyQ[q.symbol]=q;_fyQ[_fS(q.symbol)]=q;vol+=(q.volume||0);}
    const el=document.getElementById('tickerIngest');
    if(el)el.textContent=vol.toLocaleString('en-IN')+' vol (Fyers live)';
    document.querySelectorAll('[data-live-symbol]').forEach(c=>{
      const q=_fyQ[c.dataset.liveSymbol];if(!q)return;
      const f=c.dataset.liveField||'ltp',v=q[f];if(v==null)return;
      if(f==='ltp')c.textContent='\u20b9'+Number(v).toLocaleString('en-IN',{minimumFractionDigits:2,maximumFractionDigits:2});
      else if(f==='change_pct'){c.textContent=(v>=0?'+':'')+Number(v).toFixed(2)+'%';c.style.color=v>=0?'#3fb950':'#f85149';}
      c.style.transition='background .3s';c.style.background='#1a3028';
      setTimeout(()=>{c.style.background='';},700);
    });
  }catch(e){console.warn('Fyers price refresh failed',e);}
}
async function fyersGetAuthUrl(){
  const btn=document.getElementById('fyersGetUrlBtn');
  btn.textContent='Generating...';btn.disabled=true;
  try{
    const r=await fetch('/api/live/authenticate',{method:'POST'}),d=await r.json();
    document.getElementById('fyersAuthUrl').href=d.auth_url;
    document.getElementById('fyersAuthUrl').textContent=d.auth_url;
    document.getElementById('fyersAuthUrlBox').style.display='block';
  }catch(e){document.getElementById('fyersAuthStatus').innerHTML='<span style="color:#f85149">Failed: '+e.message+'</span>';}
  finally{btn.textContent='Generate Fyers Login URL';btn.disabled=false;}
}
async function fyersSubmitToken(){
  const raw=document.getElementById('fyersCodeInput').value.trim();
  const st=document.getElementById('fyersAuthStatus');
  if(!raw){st.innerHTML='<span style="color:#f85149">Paste the redirect URL or auth_code first.</span>';return;}
  let code=raw;
  try{const u=new URL(raw);code=u.searchParams.get('auth_code')||u.searchParams.get('code')||raw;}catch{}
  st.innerHTML='<span style="color:#8b949e">Connecting to Fyers...</span>';
  try{
    const r=await fetch('/api/live/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({auth_code:code})});
    const d=await r.json();
    if(r.ok&&d.status==='ok'){
      st.innerHTML='<span style="color:#3fb950">Connected: '+d.message+'</span>';
      setTimeout(()=>{document.getElementById('fyersAuthModal').style.display='none';fyersPollStatus();},2000);
    }else{st.innerHTML='<span style="color:#f85149">'+(d.detail||JSON.stringify(d))+'</span>';}
  }catch(e){st.innerHTML='<span style="color:#f85149">'+e.message+'</span>';}
}
document.addEventListener('DOMContentLoaded',()=>{
  fyersPollStatus();
  setInterval(fyersPollStatus,30000);
  setInterval(fyersRefreshPrices,15000);
});
</script>
"""

if "</body>" in html and 'id="fyersAuthModal"' not in html:
    html = html.replace("</body>", FYERS_BLOCK + "\n</body>")
    patches += 1
    print("PATCH 2 OK: Fyers auth modal + JS injected")
else:
    print("PATCH 2 SKIP: modal already present or </body> not found")

# Write back as bytes with surrogateescape encoding so we don't corrupt the file
DASHBOARD.write_bytes(html.encode("utf-8", errors="surrogateescape"))
print(f"\nDone: {orig:,} -> {len(html):,} chars  |  {patches} patches applied")

# Verify the written file is non-empty
written = DASHBOARD.stat().st_size
print(f"Verified file size: {written:,} bytes")
assert written > 50_000, "ERROR: File is suspiciously small!"
print("All OK.")

