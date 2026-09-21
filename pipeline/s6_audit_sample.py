"""Stage 6 -- reproducible random audit sample.

Screening QC is only as strong as the sample it rests on, so the sample must be
reproducible: same seed, same sample, and a third party can regenerate it.

Two modes:
    uniform      N records drawn from the whole pool
    stratified   N records drawn proportionally within each code, so rare codes
                 actually appear. (Uniform sampling of 50 from a pool where R1 is
                 11% and X9 is 0.1% will almost never show you a coding error in
                 the rare codes.)

Output: <dir>/sample_<N>.csv plus a readable dump on stdout, ordered by code, so
a human can read it straight down and mark it up.

usage
    python s6_audit_sample.py --dir <workdir> [--protocol p.json]
        --n 50 --seed 500 [--stratified] [--stratify-by code|form]
"""

import argparse
import os
import random
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=500)
    ap.add_argument("--stratified", action="store_true")
    ap.add_argument("--stratify-by", default="code", choices=["code", "form"])
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    src = os.path.join(base, "all_decisions_normalized.json")
    if not os.path.exists(src):
        src = os.path.join(base, "all_decisions.json")
    if not os.path.exists(src):
        P.banner("s6", "no decisions map -- run s3/s4 first")
        return 2
    dec = {int(k): v for k, v in P.read_json(src).items()}
    book = {P.as_int(r.get(P.id_column(proto))): r for r in P.load_workbook(proto, base)}
    pool = {i: v for i, v in dec.items() if i in book}
    P.banner("s6", f"source {os.path.basename(src)}   pool {len(pool)}")

    key = "c" if a.stratify_by == "code" else "form"
    groups = {}
    for i, x in pool.items():
        groups.setdefault(str(x.get(key, "")), []).append(i)

    rng = random.Random(a.seed)
    if a.stratified:
        pick = []
        for k, ids in sorted(groups.items()):
            want = max(1, round(a.n * len(ids) / len(pool)))
            pick.extend(rng.sample(sorted(ids), min(want, len(ids))))
        pick = pick[:a.n]
    else:
        pick = rng.sample(sorted(pool), min(a.n, len(pool)))
    pick.sort()

    sch = P.schema(proto)
    rfield = sch.get("reason_field", "r")
    sfield = sch.get("evidence_field", "s")
    efield = sch.get("effect_field", "eff")
    meta = P.workbook_spec(proto).get("meta") or {}

    rows = []
    for i in pick:
        x, b = pool[i], book[i]
        fam = str(x.get("d", "")).upper()[:1]
        row = {"idx": i, "citation_id": b.get(meta.get("citation_id", "citation_id"), ""),
               "family": fam, "code": x.get("c", ""),
               "code_meaning": P.code_meaning(proto, x.get("c")),
               "platform_label": P.label_name(proto, P.label_value(proto, fam)),
               "reason": x.get(rfield, ""), "evidence": x.get(sfield, ""),
               "effect": x.get(efield, ""), "title": b.get("title", ""),
               "abstract_chars": len(b.get("abstract") or "")}
        for f in P.group_fields(proto):
            row[f] = x.get(f, "")
        for k, column in meta.items():
            if k not in ("citation_id",):
                row[k] = b.get(column, "")
        rows.append(row)

    cols = (["idx", "citation_id", "family", "code", "code_meaning", "platform_label"]
            + P.group_fields(proto) + ["reason", "evidence", "effect"]
            + [k for k in meta if k != "citation_id"] + ["title", "abstract_chars"])
    out = P.write_csv(os.path.join(base, f"sample_{len(pick)}.csv"), rows, cols)
    P.banner("s6", f"written: {out}   (n={len(pick)}, seed={a.seed}, "
                   f"mode={'stratified by ' + key if a.stratified else 'uniform'})")

    print(f"\n  sample codes: {dict(Counter(r['code'] for r in rows).most_common())}")
    print(f"  pool codes:   {dict(Counter(str(v.get('c', '')) for v in pool.values()).most_common())}")

    print("\n" + "=" * 100)
    cur = None
    for row in rows:
        if row["code"] != cur:
            cur = row["code"]
            print(f"\n########## {cur} -- {row['code_meaning']} ##########")
        print(f"\n[idx {row['idx']} | {row['code']} | {row['platform_label']}] {row['title']}")
        print(f"   form={row.get('form','')} exp={row.get('exp','')} out={row.get('out','')} "
              f"abs={row['abstract_chars']} chars")
        print(f"   reason: {row['reason']}")
        if row["evidence"]:
            print(f"   evidence: {str(row['evidence'])[:120]}")
        if row["effect"]:
            print(f"   verbatim effect size: {row['effect']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
