"""Stage 4 -- apply ONLY definitional normalisations, and export the rest for humans.

The rule that matters here is the one that is **not** applied.

A first pass of cross-batch coding always drifts a little. The temptation is to
"fix" every inconsistency mechanically. That is how a clean dataset gets
corrupted: in the cholecystectomy review, an attempt to normalise the ambiguous
X2-vs-X3 boundary off the `form` field would have rewritten **373 decisions that
were already correct** (the model had filled `form` with the study DESIGN -- a
cohort of GBC patients -- rather than the OUTCOME kind). It was reverted.

So this stage draws a hard line:

    normalize_rules   applied automatically -- only rules that follow from the
                      definition of a code (e.g. "a systematic review has no
                      original data, therefore it is R1"), never from a hunch.
    audit_exports     written to CSV, never applied -- ambiguous boundaries where
                      both codes lead to the same downstream fate. A human reads
                      them. The protocol file carries the `description` so the
                      next person knows why each export exists.

Every applied change is recorded in `c_normalized_from`, and a code change keeps
`d` consistent with its new family (an `X` record must never end up with `c=R1`).

usage
    python s4_normalize.py --dir <workdir> [--protocol p.json]        # report only
    python s4_normalize.py --dir <workdir> --apply                    # write
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402

# audit-export column name -> decision field
_FIELD_MAP = {"code": "c", "reason": "r", "evidence": "s", "family": "d"}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--apply", action="store_true",
                    help="write all_decisions_normalized.json")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    src = os.path.join(base, "all_decisions.json")
    if not os.path.exists(src):
        P.banner("s4", f"missing {src} -- run s3_merge.py first")
        return 2
    dec = {int(k): v for k, v in P.read_json(src).items()}
    book = {P.as_int(r.get(P.id_column(proto))): r for r in P.load_workbook(proto, base)}
    P.banner("s4", f"records: {len(dec)}")

    # --- applied rules ------------------------------------------------------
    changes = []
    for i, x in sorted(dec.items()):
        fam = P.code_to_family(proto, x.get("c"))
        for rule in P.normalize_rules(proto):
            if not P.rule_matches(rule.get("when"), x, fam):
                continue
            new = rule.get("set") or {}
            nc = str(new.get("c", x.get("c", ""))).upper()
            nd = str(new.get("d", x.get("d", ""))).upper()[:1]
            if nc == str(x.get("c", "")).upper() and nd == str(x.get("d", "")).upper()[:1]:
                continue
            changes.append({"i": i, "old_code": x.get("c"), "new_code": nc,
                            "old_d": x.get("d"), "new_d": nd,
                            "rule": rule.get("id", "?"), "why": rule.get("description", ""),
                            "title": (book.get(i) or {}).get("title", "")})
            break

    print(f"\n  applied normalisations: {len(changes)}")
    for ch in changes[:40]:
        print(f"    i={ch['i']:<7} {ch['old_code']} -> {ch['new_code']} "
              f"(rule {ch['rule']})  {str(ch['title'])[:60]}")
    if len(changes) > 40:
        print(f"    ... and {len(changes) - 40} more")
    n_by_rule = Counter(ch["rule"] for ch in changes)
    for rid, n in sorted(n_by_rule.items()):
        rule = next((r for r in P.normalize_rules(proto) if r.get("id") == rid), {})
        print(f"    rule {rid}: {n}   {rule.get('description', '')[:100]}")

    # --- audit exports (never applied) --------------------------------------
    for spec in P.audit_exports(proto):
        rows = []
        for i, x in sorted(dec.items()):
            fam = P.code_to_family(proto, x.get("c"))
            if not P.rule_matches(spec.get("when"), x, fam):
                continue
            row = {}
            for col in spec.get("columns", []):
                if col == "title":
                    row[col] = (book.get(i) or {}).get("title", "")
                else:
                    row[col] = x.get(_FIELD_MAP.get(col, col), "")
            row = {"idx": i, **row}
            rows.append(row)
        dst = P.write_csv(os.path.join(base, spec["file"]), rows,
                          ["idx"] + list(spec.get("columns", [])))
        dist = Counter(r.get("code", "") for r in rows)
        print(f"\n  audit export '{spec.get('name', spec['file'])}': {len(rows)} rows "
              f"({dict(dist)}) -> {dst}")
        print(f"    {spec.get('description', '')}")
        print("    (NOT applied automatically -- for human review)")

    if not a.apply:
        print("\n  (dry run) nothing written. Add --apply to write "
              "all_decisions_normalized.json")
        return 0

    for ch in changes:
        i = ch["i"]
        dec[i] = dict(dec[i], c=ch["new_code"], d=ch["new_d"],
                      c_normalized_from=dec[i].get("c"),
                      normalize_rule=ch["rule"])
    out = os.path.join(base, "all_decisions_normalized.json")
    P.write_json(out, {str(k): v for k, v in sorted(dec.items())})
    P.banner("s4", f"written: {out}  ({len(changes)} codes normalised)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
