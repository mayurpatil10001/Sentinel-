"""Debug detection thresholds on real data."""
import sys, time
sys.path.insert(0, 'd:/Sentinel')

from data.ingest.fyers_stream import FyersMarketDataAdapter
from app.workers.detection_worker import run_detection_for_symbol

a = FyersMarketDataAdapter()

SYMBOLS = [
    ('YESBANK',    'NSE:YESBANK-EQ',    'EQUITY'),
    ('ZOMATO',     'NSE:ZOMATO-EQ',     'EQUITY'),
    ('ADANIENT',   'NSE:ADANIENT-EQ',   'EQUITY'),
    ('TATAMOTORS', 'NSE:TATAMOTORS-EQ', 'EQUITY'),
    ('TATASTEEL',  'NSE:TATASTEEL-EQ',  'EQUITY'),
    ('NIFTY50',    'NSE:NIFTY50-INDEX', 'INDEX'),
]

print(f"{'Symbol':<14} {'Last ret%':>10} {'Z-score':>8} {'Vol ratio':>10}  Detections")
print("-"*60)
for sym, fsym, itype in SYMBOLS:
    try:
        h = a.get_history(fsym, resolution='D', days=35)
        bars = [b for b in h if b.get('close') and b.get('volume') and b.get('open')]
        if len(bars) < 5:
            print(f"{sym:<14}  only {len(bars)} bars")
            time.sleep(0.3)
            continue
        baseline = bars[:-1]
        last = bars[-1]
        bvols = [b['volume'] for b in baseline]
        avg_vol = sum(bvols)/len(bvols)
        vol_ratio = last['volume'] / avg_vol
        rets = [(b['close']-b['open'])/b['open']*100 for b in baseline]
        mean_r = sum(rets)/len(rets)
        std_r = (sum((r-mean_r)**2 for r in rets)/(len(rets)-1))**0.5
        today_r = (last['close']-last['open'])/last['open']*100
        z = (today_r - mean_r)/std_r if std_r > 0 else 0

        # 3-day runup
        if len(bars) >= 5:
            runup = (bars[-3]['close']-bars[-5]['close'])/bars[-5]['close']*100
        else:
            runup = 0

        dets = run_detection_for_symbol(sym, fsym, a, itype)
        det_names = [d['pattern_type'] for d in dets]
        print(f"{sym:<14}  {today_r:>8.2f}%  {z:>7.2f}  {vol_ratio:>8.1f}x  {det_names}")
        time.sleep(0.3)
    except Exception as e:
        print(f"{sym:<14}  ERROR: {e}")
        time.sleep(1)

print()
print("Done.")
