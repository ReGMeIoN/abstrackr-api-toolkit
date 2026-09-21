"""Stage 1 -- split the unique-record workbook into screening batches.

Input   <dir>/<workbook.records>              the de-duplicated workbook
Output  <outdir>/batches/<prefix>NNN.json     {"batch","n","records":[{i,y,j,t,a}, ...]}
        <outdir>/batches/manifest.csv          batch -> n_records
        <outdir>/batches/_INDEX.json           record id -> batch name

The batch payload is deliberately tiny (`i` id, `y` year, `j` journal, `t` title,
`a` abstract) because those five keys are what a screening subagent needs and
nothing else. Keeping the payload small is a cost decision: the agent reads the
title/abstract, not the whole record.

**Batch size.** The default is 250. This is not arbitrary -- see
docs/PIPELINE.md, "Orchestration discipline". One 100-record batch per subagent
turns 8,500 records into 85 agent startups and multiplies the token cost of the
work by 2-4x. 200-300 records per batch is the sweet spot unless each record
demands long reasoning.

usage
    python s1_prep_batches.py --dir <workdir> [--protocol p.json]
        [--size 250] [--prefix b_] [--only-missing] [--outdir <dir>]
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--size", type=int, default=250, help="records per batch (default 250)")
    ap.add_argument("--prefix", default="b_", help="batch filename prefix")
    ap.add_argument("--batch-dir", default="batches")
    ap.add_argument("--dec-dir", default="decisions",
                    help="used by --only-missing to find batches already decided")
    ap.add_argument("--only-missing", action="store_true",
                    help="skip record ids that already have a decision on disk")
    ap.add_argument("--outdir", default=None, help="write elsewhere (default: --dir)")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    out = P.outdir(a)
    bdir = os.path.join(out, a.batch_dir)
    os.makedirs(bdir, exist_ok=True)

    rows = P.load_workbook(proto, base)
    idcol = P.id_column(proto)
    P.banner("s1", f"workbook {os.path.basename(P.workbook_path(proto, base))}: "
                   f"{len(rows)} records")

    done = set()
    if a.only_missing:
        ddir = os.path.join(out, a.dec_dir)
        if os.path.isdir(ddir):
            for fn in sorted(os.listdir(ddir)):
                if fn.endswith(".decisions.json"):
                    try:
                        d = P.read_json(os.path.join(ddir, fn))
                    except Exception:  # noqa: BLE001
                        continue
                    for x in d.get("decisions", []):
                        i = P.as_int(x.get("i"))
                        if i is not None:
                            done.add(i)
        P.banner("s1", f"already decided: {len(done)}")

    todo = [r for r in rows if P.as_int(r.get(idcol)) not in done]
    P.banner("s1", f"to batch: {len(todo)}  (size={a.size}, prefix={a.prefix})")

    manifest, index = [], {}
    for n, start in enumerate(range(0, len(todo), a.size), 1):
        chunk = todo[start:start + a.size]
        name = f"{a.prefix}{n:03d}"
        payload = [P.batch_record(proto, r) for r in chunk]
        P.write_json(os.path.join(bdir, name + ".json"),
                     {"batch": name, "n": len(payload), "records": payload})
        for r in payload:
            index[str(r["i"])] = name
        manifest.append({"batch": name, "n_records": len(payload)})

    P.write_csv(os.path.join(bdir, "manifest.csv"), manifest, ["batch", "n_records"])
    P.write_json(os.path.join(bdir, "_INDEX.json"), index, indent=0)

    sizes = Counter(m["n_records"] for m in manifest)
    P.banner("s1", f"batches: {len(manifest)} -> {bdir}")
    P.banner("s1", f"size distribution: {dict(sorted(sizes.items()))}")
    P.banner("s1", f"total batched: {sum(m['n_records'] for m in manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
