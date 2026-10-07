"""Restore a backup_dump.py backup into a scratch SQLite DB and verify it.

Usage: python scripts/restore_check.py <backup-dir>   (the extracted bk/db folder)

Builds the schema from the app's own models, loads every JSONL table, then checks
row counts against MANIFEST.json and that every restored row equals the dumped row. A backup that has not passed this is a hope, not a backup.
"""
import base64, datetime, decimal, json, os, sys, tempfile
os.environ.pop("DATABASE_URL", None)
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="restore_check_")
import sqlalchemy as sa
from app import create_app
from models import db

src = sys.argv[1]
man = json.load(open(os.path.join(src, "MANIFEST.json")))["tables"]

def dec(v):
    if isinstance(v, dict) and "__t" in v:
        t, x = v["__t"], v["v"]
        return {"dt": datetime.datetime.fromisoformat, "d": datetime.date.fromisoformat,
                "dec": decimal.Decimal, "b": base64.b64decode}[t](x)
    return v

def enc(v):
    if isinstance(v, datetime.datetime): return {"__t": "dt", "v": v.isoformat()}
    if isinstance(v, datetime.date): return {"__t": "d", "v": v.isoformat()}
    if isinstance(v, decimal.Decimal): return {"__t": "dec", "v": str(v)}
    if isinstance(v, (bytes, memoryview)): return {"__t": "b", "v": base64.b64encode(bytes(v)).decode()}
    return v

app = create_app(testing=True)
bad = 0
with app.app_context():
    db.drop_all(); db.create_all()
    with db.engine.begin() as c:
        meta = sa.MetaData(); meta.reflect(bind=c)
        for t, m in man.items():
            if t == "alembic_version": continue
            rows = [{k: dec(v) for k, v in json.loads(l).items()} for l in open(os.path.join(src, t + ".jsonl"))]
            if rows: c.execute(meta.tables[t].insert(), rows)
        for t, m in man.items():
            if t == "alembic_version": continue
            tbl = meta.tables[t]
            got = c.execute(sa.select(tbl)).mappings().all()
            canon = lambda r: json.dumps({k: enc(r[k]) for k in m["columns"]}, sort_keys=True, default=str)
            want = sorted(json.dumps(json.loads(l), sort_keys=True, default=str)
                          for l in open(os.path.join(src, t + ".jsonl")))
            same = want == sorted(canon(r) for r in got)
            ok = len(got) == m["rows"] and same
            print(f"{'OK ' if ok else 'BAD'} {t:28} manifest={m['rows']:5} restored={len(got):5} contents={'match' if same else 'DIFFER'}")
            bad += (not ok)
print("RESTORE", "PASSED" if not bad else f"FAILED ({bad} tables)")
sys.exit(1 if bad else 0)
