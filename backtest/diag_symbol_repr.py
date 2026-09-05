"""
Diagnostic: verify repr() of symbol values from live NSE bhavcopy.
Run with: python backtest/diag_symbol_repr.py
"""
import sys
sys.path.insert(0, '.')
from datetime import date
from data.ingest.nse_bhavcopy import fetch_bhavcopy

print("Fetching bhavcopy for 2018-01-02 (first NSE trading day of 2018)...")
df = fetch_bhavcopy(date(2018, 1, 2), include_delivery=False)
print(f"Total EQ rows after fix: {len(df)}")
print()

# 1. repr() of first 10 symbol values — reveals trailing/leading whitespace
print("=== repr() of first 10 symbol values ===")
for s in df["symbol"].head(10):
    print(" ", repr(s))

print()

# 2. Search for each pump-dump company name fragment
for kw in ["MAURI", "7NR", "GBL", "VISHAL", "DARJ"]:
    hits = df[df["symbol"].str.upper().str.contains(kw, na=False)]
    print(f"'{kw}': {len(hits)} rows")
    for _, row in hits.iterrows():
        print(f"    repr(symbol)={repr(row['symbol'])}  close={row['close']}  volume={row['volume']}")

print()
# 3. Confirm exact match with stripped vs unstripped
print("=== Exact match test: 'MAURIUDYOG' ===")
exact = df[df["symbol"].str.upper() == "MAURIUDYOG"]
print(f"Rows matched: {len(exact)}")
if len(exact) == 0:
    # check raw before strip was applied
    print("Symbol not present in this day's data (may be BSE-only).")
    print("A few symbols alphabetically near M:")
    m_syms = df[df["symbol"].str.upper().str.startswith("MAU", na=False)]["symbol"].head(10).tolist()
    print(" ", m_syms)
