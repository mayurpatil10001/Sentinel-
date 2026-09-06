import json, pathlib

SD   = pathlib.Path("demo/sample_data")
HTML = pathlib.Path("demo/sentinel_stakeholder_demo.html").read_text(encoding="utf-8")

idx = json.loads((SD / "watchlist_index.json").read_text(encoding="utf-8"))

print("=== CONDITION → FILE + HTML EMBEDDING ===")
all_ok = True
for asset in idx["assets"]:
    for cond in asset["conditions"]:
        key  = asset["id"] + "_" + cond["id"]
        fpath = SD / cond["file"]
        exists  = fpath.exists()
        in_html = ('"' + key + '"') in HTML
        ok = exists and in_html
        if not ok:
            all_ok = False
        print(f"  {'OK  ' if ok else 'FAIL'} {key:35s}  file={exists}  in_html={in_html}")
print(f"\nAll OK: {all_ok}")

print("\n=== CONDITION COUNTS ===")
expected = {"RELIANCE": 2, "KAVITIND": 3, "TINYLTD": 2, "NIFTYFUT": 2, "NIFTYOPT": 3}
for asset in idx["assets"]:
    n    = len(asset["conditions"])
    exp  = expected.get(asset["id"], "?")
    ok   = n == exp
    ids  = [c["id"] for c in asset["conditions"]]
    print(f"  {'OK  ' if ok else 'FAIL'} {asset['id']:12s} {n} conditions (expected {exp}): {ids}")

print("\n=== NORMAL → fired=False, ALERTS → fired=True ===")
for f in sorted(SD.glob("watchlist_*.json")):
    if f.name == "watchlist_index.json":
        continue
    d      = json.loads(f.read_text(encoding="utf-8"))
    out    = d.get("output") or {}
    fired  = out.get("fired")
    normal = "_normal" in f.name
    exp    = False if normal else True
    ok     = fired == exp
    score  = out.get("score")
    sev    = out.get("severity", "")
    score_str = f"score={score:.3f}  sev={sev}" if score is not None else "no-score"
    print(f"  {'OK  ' if ok else 'FAIL'} {f.name:45s} fired={fired}  {score_str}")

print("\n=== VERBATIM TEXT CHECK: KAVITIND circular ===")
jf   = json.loads((SD / "watchlist_KAVITIND_circular.json").read_text(encoding="utf-8"))
expl = jf["output"].get("explanation", "")
print(f"  JSON  first 120: {repr(expl[:120])}")
print(f"  In HTML verbatim: {expl[:80] in HTML}")

print("\n=== KEY HTML STRUCTURAL MARKERS ===")
checks = [
    ("Nav brand",           "SENTINEL // MARKET SURVEILLANCE"),
    ("Watchlist panel",     "wl-panel"),
    ("Run button",          "Run Surveillance Scan"),
    ("Inline note",         "How this works"),
    ("Result panel",        "res-panel"),
    ("Honest Status",       "What Is and Is Not Proven"),
    ("Negative control",    "False Positive Rate"),
    ("Engineering section", "Performance, Correctness"),
    ("Architecture",        "Architecture"),
    ("WATCHLIST_INDEX JS",  "const WATCHLIST_INDEX"),
    ("WATCHLIST_DATA JS",   "const WATCHLIST_DATA"),
    ("State accumulation",  "assetScans"),
    ("Guard message",       "No data file found for"),
    ("Fabrication guard",   "cannot display a combination without one"),
]
for label, needle in checks:
    found = needle in HTML
    print(f"  {'OK  ' if found else 'MISS'} {label}")
