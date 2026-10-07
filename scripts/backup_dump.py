"""Logical dump of every table to JSONL + manifest. Read-only against the DB."""
import os, sys, json, base64, hashlib, datetime, decimal, sqlalchemy as sa
out = sys.argv[1]; os.makedirs(out, exist_ok=True)
e = sa.create_engine(os.environ["DATABASE_URL"].replace("postgresql://","postgresql+psycopg2://",1))
def enc(v):
    if isinstance(v, (datetime.datetime, datetime.date)): return {"__t":"dt" if isinstance(v,datetime.datetime) else "d","v":v.isoformat()}
    if isinstance(v, decimal.Decimal): return {"__t":"dec","v":str(v)}
    if isinstance(v, (bytes, memoryview)): return {"__t":"b","v":base64.b64encode(bytes(v)).decode()}
    return v
man = {}
with e.connect() as c:
    c.execute(sa.text("SET TRANSACTION READ ONLY ISOLATION LEVEL REPEATABLE READ"))
    insp = sa.inspect(e)
    for t in sorted(insp.get_table_names()):
        cols = [x["name"] for x in insp.get_columns(t)]
        rows = c.execute(sa.text(f'select * from "{t}"')).mappings().all()
        p = os.path.join(out, t + ".jsonl")
        with open(p, "w") as f:
            for r in rows: f.write(json.dumps({k: enc(r[k]) for k in cols}) + "\n")
        man[t] = {"rows": len(rows), "sha256": hashlib.sha256(open(p,"rb").read()).hexdigest(), "columns": cols}
json.dump({"taken_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "tables": man}, open(os.path.join(out,"MANIFEST.json"),"w"), indent=1)
print("dumped", len(man), "tables,", sum(m["rows"] for m in man.values()), "rows")
