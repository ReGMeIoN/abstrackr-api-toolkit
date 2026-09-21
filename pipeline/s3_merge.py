"""Stage 3 -- merge every decisions/*.decisions.json into one map + a workbook.

Two outputs that everything downstream depends on:

    all_decisions.json        {record id: decision}   the fused judgement map
    screening_decisions.csv   one row per workbook record, decision columns first
    merge_summary.json        counts by family, by code, by form (+ coverage)

Coverage is checked loudly and the exit code is non-zero when anything is
missing, because a **partial pass must never be submitted to the platform**: a
half-filled decision map would silently label only part of the pool, and the
unlabelled remainder is indistinguishable from "not screened yet".

usage
    python s3_merge.py --dir <workdir> [--protocol p.json] [--dec-dir decisions]
"""

import argparse
import glob
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--dec-dir", default="decisions")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    files = sorted(glob.glob(os.path.join(base, a.dec_dir, "*.decisions.json")))
    if not files:
        P.banner("s3", f"no *.decisions.json under {os.path.join(base, a.dec_dir)}")
        return 2

    merged, dupes, bad = {}, [], []
    for path in files:
        try:
            d = P.read_json(path)
        except Exception as exc:  # noqa: BLE001
            bad.append((os.path.basename(path), str(exc)))
            continue
        for x in d.get("decisions", []):
            i = P.as_int(x.get("i"))
            if i is None:
                continue
            if i in merged:
                dupes.append(i)
            merged[i] = x

    P.banner("s3", f"decision files: {len(files)}   merged records: {len(merged)}")
    if bad:
        print(f"  !! unreadable: {bad}")
    if dupes:
        print(f"  !! decided twice (last wins): {len(dupes)} e.g. {sorted(dupes)[:10]}")

    out = os.path.join(base, "all_decisions.json")
    P.write_json(out, {str(k): v for k, v in sorted(merged.items())})
    P.banner("s3", f"written: {out}")

    book = P.load_workbook(proto, base)
    idcol = P.id_column(proto)
    want = {P.as_int(r.get(idcol)) for r in book}
    have = set(merged)
    missing = sorted(x for x in (want - have) if x is not None)
    extra = sorted(x for x in (have - want) if x is not None)
    P.banner("s3", f"workbook {len(book)}   decided {len(have & want)}   "
                   f"missing {len(missing)}   extra {len(extra)}")
    if missing:
        print(f"  MISSING (first 20): {missing[:20]}")
        P.write_json(os.path.join(base, "missing_decisions.json"), missing)
        print("  -> missing_decisions.json written (do NOT submit this pass)")
    if extra:
        print(f"  EXTRA (not in workbook): {len(extra)} e.g. {extra[:10]}")

    # --- per-record workbook ------------------------------------------------
    sch = P.schema(proto)
    rfield = sch.get("reason_field", "r")
    sfield = sch.get("evidence_field", "s")
    cfield = sch.get("confidence_field", "conf")
    efield = sch.get("effect_field", "eff")
    meta = P.workbook_spec(proto).get("meta") or {}
    cols = (["idx", "d", "c"] + P.group_fields(proto)
            + ["need", cfield, efield, rfield, sfield]
            + [k for k in meta if k != "citation_id"] + ["title"])
    rows = []
    for r in book:
        i = P.as_int(r.get(idcol))
        x = merged.get(i, {})
        need = x.get("need")
        if isinstance(need, str):
            need = [t.strip() for t in need.split(",") if t.strip()]
        row = {"idx": i, "d": x.get("d", ""), "c": x.get("c", ""),
               "need": "|".join(need or []),
               cfield: x.get(cfield, ""), efield: x.get(efield, ""),
               rfield: x.get(rfield, ""), sfield: x.get(sfield, ""),
               "title": r.get("title", "")}
        for k, column in meta.items():
            if k != "citation_id":
                row[k] = r.get(column, "")
        for f in P.group_fields(proto):
            row[f] = x.get(f, "")
        rows.append(row)
    dec_csv = P.write_csv(os.path.join(base, "screening_decisions.csv"), rows, cols)
    P.banner("s3", f"written: {dec_csv}  ({len(rows)} rows)")

    by_d = Counter(str(v.get("d", "")).upper()[:1] for v in merged.values())
    by_c = Counter(str(v.get("c", "")) for v in merged.values())
    by_form = Counter(str(v.get("form", "")) for v in merged.values() if v.get("form"))
    summary = {"files": len(files), "decided": len(merged), "workbook": len(book),
               "missing": len(missing), "extra": len(extra),
               "by_decision": dict(by_d), "by_code": dict(by_c),
               "by_form": dict(by_form)}
    P.write_json(os.path.join(base, "merge_summary.json"), summary)
    print("\n  by family: " + ", ".join(f"{k}={v}" for k, v in sorted(by_d.items())))
    print("  by code:   " + ", ".join(f"{k}={v}" for k, v in by_c.most_common()))
    hold = sum(v for k, v in by_d.items() if k in P.hold_families(proto))
    print(f"  Hold pool (families {P.hold_families(proto)}): {hold}")
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
