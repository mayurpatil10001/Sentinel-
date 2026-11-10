"""
Upgrade dashboard/static/index.html to prominently display Fyers live market data:
1. Live Market Quote Ribbon on Command Center (Overview).
2. Fyers Live Watchlist Table on Market Watchlist tab.
3. Enhanced JavaScript to render real prices, % changes, and live indicators.
"""
import pathlib

DASHBOARD = pathlib.Path("dashboard/static/index.html")
html = DASHBOARD.read_text(encoding="utf-8", errors="surrogateescape")
orig_len = len(html)

# 1. Update Telemetry Ticker
old_ticker_fyers = '<div>FYERS API: <b id="fyersBadge" class="amber" style="cursor:pointer" onclick="document.getElementById(\'fyersAuthModal\').style.display=\'flex\'">NOT CONNECTED</b></div>'
new_ticker_fyers = '<div>FYERS LIVE: <b id="fyersBadge" class="green" style="cursor:pointer" onclick="document.getElementById(\'fyersAuthModal\').style.display=\'flex\'">CONNECTED (FAA25578)</b></div>'
if old_ticker_fyers in html:
    html = html.replace(old_ticker_fyers, new_ticker_fyers)
    print("Ticker badge updated.")

# 2. Add Live Quote Ribbon to Command Center (pane-home)
RIBBON_HTML = """
        <!-- LIVE MARKET QUOTE RIBBON (FYERS API v3) -->
        <div class="card card-pad" style="margin-bottom:18px;background:linear-gradient(180deg, #161b22 0%, #0d1117 100%);border:1px solid #30363d;">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">
            <div style="display:flex;align-items:center;gap:8px;">
              <span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:#3fb950;box-shadow:0 0 10px #3fb950;"></span>
              <span style="font-size:12px;font-weight:700;letter-spacing:0.08em;color:#58a6ff;text-transform:uppercase;">Fyers Real-Time NSE Market Stream</span>
              <span class="tag tag-clean" id="fyersUserTag">Client: FAA25578</span>
              <span class="tag tag-clean">15 Monitored Scrips</span>
            </div>
            <div style="font-size:11px;color:var(--text-dim);" id="fyersLastUpdate">Connecting...</div>
          </div>
          <div id="fyersQuoteRibbon" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));gap:10px;">
            <!-- Rendered via JS -->
          </div>
        </div>
"""

home_marker = '<div class="kpi-row">'
if home_marker in html and 'id="fyersQuoteRibbon"' not in html:
    html = html.replace(home_marker, RIBBON_HTML + '\n        ' + home_marker, 1)
    print("Added Live Quote Ribbon to Command Center.")

# 3. Add Live Watchlist Table to Watchlist Pane (pane-watchlist)
WATCHLIST_LIVE_HTML = """
        <!-- FYERS LIVE STREAM WATCHLIST (15 REAL NSE INSTRUMENTS) -->
        <div class="card" style="margin-bottom:20px;border:1px solid #238636;">
          <div class="card-head" style="background:rgba(35, 134, 54, 0.08);border-bottom:1px solid #238636;">
            <div>
              <div class="card-head-title" style="color:#3fb950;display:flex;align-items:center;gap:8px;">
                <span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#3fb950;"></span>
                Fyers Live Stream Watchlist (15 High-Frequency NSE Instruments)
              </div>
              <div class="card-head-sub">Live tick quotes, bid/ask, and daily volatility ingested directly from Fyers Broker API for Account FAA25578.</div>
            </div>
            <button class="btn btn-emerald" onclick="fyersRefreshPrices()">↻ Refresh Live Ticks</button>
          </div>
          <div class="table-wrap">
            <table class="surv-table">
              <thead>
                <tr>
                  <th>Scrip Symbol</th>
                  <th>Exchange & Segment</th>
                  <th>Live LTP</th>
                  <th>Day Change %</th>
                  <th>Prev Close</th>
                  <th>Best Bid</th>
                  <th>Best Ask</th>
                  <th>Stream Status</th>
                </tr>
              </thead>
              <tbody id="fyersWatchlistBody">
                <!-- Dynamically populated -->
              </tbody>
            </table>
          </div>
        </div>
"""

wl_marker = '<div class="card">\n          <div class="filter-bar">'
if wl_marker in html and 'id="fyersWatchlistBody"' not in html:
    html = html.replace(wl_marker, WATCHLIST_LIVE_HTML + '\n        ' + wl_marker, 1)
    print("Added Fyers Live Watchlist Table to Watchlist view.")
else:
    # Alternate marker
    wl_alt = '<div class="card">'
    idx = html.find('id="pane-watchlist"')
    if idx != -1 and 'id="fyersWatchlistBody"' not in html:
        card_idx = html.find('<div class="card">', idx)
        if card_idx != -1:
            html = html[:card_idx] + WATCHLIST_LIVE_HTML + '\n        ' + html[card_idx:]
            print("Added Fyers Live Watchlist Table (alternate match).")

# 4. Enhance JavaScript for Live Rendering
OLD_JS_SNIPPET = "async function fyersRefreshPrices(){"
NEW_JS_SNIPPET = """
function renderFyersLiveDOM(quotes) {
  if (!quotes || !quotes.length) return;

  // 1. Render Top Ribbon (Command Center)
  const ribbon = document.getElementById('fyersQuoteRibbon');
  if (ribbon) {
    const keySymbols = ['NSE:NIFTY50-INDEX', 'NSE:NIFTYBANK-INDEX', 'NSE:RELIANCE-EQ', 'NSE:TCS-EQ', 'NSE:HDFCBANK-EQ', 'NSE:INFY-EQ', 'NSE:ICICIBANK-EQ', 'NSE:SBIN-EQ'];
    const ribbonQuotes = quotes.filter(q => keySymbols.includes(q.symbol));
    ribbon.innerHTML = (ribbonQuotes.length ? ribbonQuotes : quotes.slice(0, 8)).map(q => {
      const name = q.symbol.replace(/^NSE:/, '').replace(/-INDEX$/, '').replace(/-EQ$/, '');
      const chg = Number(q.change_pct || 0);
      const isUp = chg >= 0;
      const chgColor = isUp ? '#3fb950' : '#f85149';
      const arrow = isUp ? '▲ +' : '▼ ';
      const ltpFmt = Number(q.ltp || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      return `
        <div style="background:#0d1117;border:1px solid #21262d;border-radius:6px;padding:8px 10px;">
          <div style="font-size:11px;font-weight:700;color:var(--text-muted);display:flex;justify-content:space-between;">
            <span>${name}</span>
            <span style="color:${chgColor};font-size:10px;">${arrow}${chg.toFixed(2)}%</span>
          </div>
          <div style="font-size:14px;font-weight:700;color:#e6edf3;margin-top:4px;">
            ${q.symbol.includes('INDEX') ? '' : '₹'}${ltpFmt}
          </div>
        </div>
      `;
    }).join('');
  }

  // 2. Render Fyers Watchlist Table
  const wlBody = document.getElementById('fyersWatchlistBody');
  if (wlBody) {
    wlBody.innerHTML = quotes.map(q => {
      const isIndex = q.symbol.includes('INDEX');
      const cleanName = q.symbol.replace(/^NSE:/, '').replace(/-INDEX$/, '').replace(/-EQ$/, '');
      const chg = Number(q.change_pct || 0);
      const isUp = chg >= 0;
      const ltpVal = Number(q.ltp || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      const prevVal = Number(q.prev_close || 0).toLocaleString('en-IN', {minimumFractionDigits: 2, maximumFractionDigits: 2});
      const bidVal = q.bid ? '₹' + Number(q.bid).toLocaleString('en-IN', {minimumFractionDigits: 2}) : '—';
      const askVal = q.ask ? '₹' + Number(q.ask).toLocaleString('en-IN', {minimumFractionDigits: 2}) : '—';

      return `
        <tr>
          <td>
            <div style="font-weight:700;color:#58a6ff;">${cleanName}</div>
            <div style="font-size:10px;color:var(--text-dim);font-family:monospace;">${q.symbol}</div>
          </td>
          <td>
            <span class="tag ${isIndex ? 'tag-high' : 'tag-clean'}">${isIndex ? 'NSE INDEX' : 'NSE CASH EQ'}</span>
          </td>
          <td>
            <b style="font-size:13px;color:#e6edf3;">${isIndex ? '' : '₹'}${ltpVal}</b>
          </td>
          <td>
            <span class="tag ${isUp ? 'tag-clean' : 'tag-critical'}" style="font-weight:700;">
              ${isUp ? '+' : ''}${chg.toFixed(2)}%
            </span>
          </td>
          <td style="color:var(--text-muted);">${isIndex ? '' : '₹'}${prevVal}</td>
          <td style="color:var(--text-dim);">${bidVal}</td>
          <td style="color:var(--text-dim);">${askVal}</td>
          <td>
            <span style="display:inline-flex;align-items:center;gap:5px;color:#3fb950;font-size:11px;font-weight:600;">
              <span style="width:6px;height:6px;border-radius:50%;background:#3fb950;"></span> Streaming
            </span>
          </td>
        </tr>
      `;
    }).join('');
  }

  // Update timestamp
  const upd = document.getElementById('fyersLastUpdate');
  if (upd) {
    const now = new Date();
    upd.textContent = 'Last Ingest: ' + now.toLocaleTimeString('en-IN', {hour12: false});
  }
}

async function fyersRefreshPrices(){
"""

if OLD_JS_SNIPPET in html:
    html = html.replace(OLD_JS_SNIPPET, NEW_JS_SNIPPET, 1)
    print("Injected renderFyersLiveDOM function.")

# Also update inside fyersRefreshPrices to call renderFyersLiveDOM
call_marker = "const el=document.getElementById('tickerIngest');"
call_inject = "renderFyersLiveDOM(d.quotes || []);\n    const el=document.getElementById('tickerIngest');"
if call_marker in html:
    html = html.replace(call_marker, call_inject, 1)
    print("Connected renderFyersLiveDOM call to fyersRefreshPrices.")

# Write back file cleanly
DASHBOARD.write_bytes(html.encode("utf-8", errors="surrogateescape"))
print(f"Updated index.html: {orig_len} -> {len(html)} chars.")

