import hashlib, json, sys
sys.path.insert(0, "data")
from stdb import sql
fp = {}
for t, key in (("health_source", "url"), ("health_chunk", "id"), ("health_assessment", "id")):
    rows = sorted(sql(f"SELECT * FROM {t}"), key=lambda r: str(r[key]))
    fp[t] = {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()[:16]}
print(json.dumps(fp))
