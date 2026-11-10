"""Quick debug script to check detection thresholds."""
from data.ingest.fyers_stream import FyersMarketDataAdapter
from app.workers.detection_worker import run_detection_for_symbol

a = FyersMarketDataAdapter()

stocks = [
    ('ADANIENT', 'NSE:ADANIENT-EQ', 'EQUITY'),
    ('VEDL', 'NSE:VEDL-EQ', 'EQUITY'),
    ('TATASTEEL', 'NSE:TATASTEEL-EQ', 'EQUITY'),
    ('YESBANK', 'NSE:YESBANK-EQ', 'EQUITY'),
    ('ZOMATO', 'NSE:ZOMATO-EQ', 'EQUITY'),
    ('TATAMOTORS', 'NSE:TATAMOTORS-EQ', 'EQUITY'),
    ('IDEA', 'NSE:IDEA-EQ', 'EQUITY'),
    ('NIFTY50', 'NSE:NIFTY50-INDEX', 'INDEX'),
]

print(f"{'Symbol':<14} {'Ret%':>6} {'Z':>6} {'VolX':>6} {'Run3d%':>8} {'Rev%':>8} {'Detections'}")
print("-" * 70)

for sym, fyers_sym, itype in stocks:
    try:
        h = a.get_history(fyers_sym, resolution='D', days=35)
        bars = [b for b in h if b.get('close') and b.get('volume')]
        if len(bars) < 5:
            print(f"{sym:<14} only {len(bars)} bars")
            continue
        
        baseline = bars[:-1]
        returns = [(b['close'] - b['open'])/b['open']*100 for b in baseline if b.get('open')]
        mean_ret = sum(returns)/len(returns) if returns else 0
        var = sum((r-mean_ret)**2 for r in returns)/(len(returns)-1) if len(returns)>1 else 0
        std_ret = var**0.5
        last = bars[-1]
        today_ret = (last['close'] - last['open'])/last['open']*100 if last.get('open') else 0
        z = (today_ret - mean_ret)/std_ret if std_ret > 0 else 0
        avg_vol = sum(b['volume'] for b in baseline)/len(baseline) if baseline else 0
        vol_ratio = last['volume']/avg_vol if avg_vol else 0
        runup = (bars[-3]['close'] - bars[-5]['close'])/bars[-5]['close']*100 if bars[-5]['close'] else 0
        reversal = (bars[-2]['close'] - bars[-3]['close'])/bars[-3]['close']*100 if bars[-3]['close'] else 0
        
        # Actually run detection
        dets = run_detection_for_symbol(sym, fyers_sym, a, itype)
        det_summary = [d['pattern_type'] for d in dets] if dets else []
        
        print(f"{sym:<14} {today_ret:>6.1f} {z:>6.1f} {vol_ratio:>6.1f} {runup:>8.1f} {reversal:>8.1f} {det_summary}")
    except Exception as e:
        print(f"{sym:<14} ERROR: {e}")
