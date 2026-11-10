"""Quick detection pipeline smoke test."""
import sys
sys.path.insert(0, 'd:/Sentinel')

from data.ingest.fyers_stream import FyersMarketDataAdapter
from app.workers.detection_worker import (
    detect_volume_spike, detect_price_zscore,
    detect_thin_volume_pump, detect_momentum_reversal
)
import time

a = FyersMarketDataAdapter()

print("Fetching YESBANK history...")
h = a.get_history('NSE:YESBANK-EQ', resolution='D', days=35)
bars = [b for b in h if b.get('close') and b.get('volume') and b.get('open')]
print(f"Got {len(bars)} bars")

if len(bars) >= 5:
    baseline = bars[:-1]
    last = bars[-1]

    bvols = [b['volume'] for b in baseline]
    avg_vol = sum(bvols) / len(bvols)
    vol_ratio = last['volume'] / avg_vol

    rets = [(b['close'] - b['open']) / b['open'] * 100 for b in baseline]
    mean_r = sum(rets) / len(rets)
    std_r = (sum((r - mean_r)**2 for r in rets) / (len(rets) - 1))**0.5
    today_r = (last['close'] - last['open']) / last['open'] * 100
    z = (today_r - mean_r) / std_r if std_r > 0 else 0

    print(f"\nLast bar: open={last['open']:.2f} close={last['close']:.2f}")
    print(f"Today return: {today_r:.2f}%  Z-score: {z:.2f}")
    print(f"Volume: {last['volume']:,}  Avg: {avg_vol:,.0f}  Ratio: {vol_ratio:.2f}x")
    print(f"Mean return: {mean_r:.2f}%  Std: {std_r:.2f}%")
    print(f"\n--- Detector calls ---")
    print("Volume spike:", detect_volume_spike('YESBANK', last['volume'], avg_vol, last['close'], today_r))
    print("Price zscore:", detect_price_zscore('YESBANK', today_r, rets, last['close']))
    print("Thin volume:", detect_thin_volume_pump('YESBANK', last['volume'], avg_vol, today_r, last['close']))
    print("Momentum:", detect_momentum_reversal('YESBANK', bars, last['close']))

    print(f"\nConclusion: {'NORMAL - no anomalies (thresholds correct)' if vol_ratio < 3 and abs(z) < 2.5 else 'ANOMALY DETECTED'}")
    print(f"Vol needs >{3}x for MEDIUM, {5}x for HIGH")
    print(f"Z needs >{2.5} for MEDIUM, {3.5} for HIGH")
print("\nDone.")
