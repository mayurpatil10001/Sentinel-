import json, pathlib
sd = pathlib.Path("demo/sample_data")
for f in sorted(sd.glob("*.json")):
    d = json.loads(f.read_text(encoding="utf-8"))
    out = d.get("output") or {}
    fired = out.get("fired")
    score = out.get("score")
    sev   = out.get("severity")
    expl  = (out.get("explanation") or "")[:120]
    print(f"{f.name}: fired={fired}  score={score}  sev={sev}")
    if expl:
        print(f"  expl: {expl}...")
