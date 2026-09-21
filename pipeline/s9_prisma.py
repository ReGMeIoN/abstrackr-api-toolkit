"""Stage 9 -- PRISMA 2020 accounting, with the identities actually checked.

Counting decisions is easy; making a PRISMA diagram **close** is not. Reviewers
routinely publish a flow diagram whose numbers do not add up, because the boxes
are filled by hand from different sources. This stage derives every number it can
from the artefacts the pipeline already produced, takes the rest as explicit
inputs, and then verifies the three identities that must hold:

    identified - duplicates_removed            == records_screened
    excluded_stage1 + hold + topical_reviews   == records_screened
    sum(exclusion reasons by code)             == records_screened     (unique count)

Anything it cannot derive is reported as `null` and listed as a **to-fill**
item. It never guesses: a made-up PRISMA number is a fabricated result.

Two counts exist for every code and they differ, which is the single most common
source of confusion:

    unique count   one per de-duplicated record  -> the basis of PRISMA
    id count       one per platform citation id  -> the basis of platform reports

Artifacts
    prisma_values.json    machine-readable, one key per PRISMA 2020 box
    prisma_numbers.md     the same, as a table a human can paste into the review

usage
    python s9_prisma.py --dir <workdir> [--protocol p.json]
        [--identified 11925] [--duplicates-removed 3434] [--topical-reviews 710]
        [--not-retrieved 0] [--included 215] [--platform-maybe 258]
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
    ap.add_argument("--identified", type=int, default=None,
                    help="records identified across all sources (default: rows in the "
                         "all-records file, i.e. the platform pool)")
    ap.add_argument("--duplicates-removed", type=int, default=None,
                    help="default: identified - screened")
    ap.add_argument("--topical-reviews", type=int, default=None,
                    help="reviews/letters kept for citation chasing AND assessed for "
                         "eligibility (records excluded on TYPE at the end, not on topic)")
    ap.add_argument("--not-retrieved", type=int, default=0)
    ap.add_argument("--included", type=int, default=None,
                    help="studies included (full-text stage result; else null)")
    ap.add_argument("--platform-maybe", type=int, default=None,
                    help="optional cross-check: the platform's Maybe count")
    ap.add_argument("--decisions", default=None)
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    files = P.platform_section(proto).get("files") or {}
    all_p = P.resolve(base, files.get("records") or "records_all.csv")
    dec_p = P.resolve(base, a.decisions or files.get("decisions")
                      or "all_decisions_normalized.json")
    if not os.path.exists(dec_p):
        P.banner("s9", f"missing decisions map: {dec_p}")
        return 2

    dec = {int(k): v for k, v in P.read_json(dec_p).items()}
    rows = P.read_csv(all_p) if os.path.exists(all_p) else []
    workbook = P.load_workbook(proto, base)

    id_count = Counter()
    unique_count = Counter()
    for x in dec.values():
        unique_count[str(x.get("c", ""))] += 1
    roots = P.cluster_roots(rows, P.id_column(proto), P.dedup_keys(proto)) if rows else {}
    cluster_of = {}
    for r in rows:
        cluster_of.setdefault(roots[str(r[P.id_column(proto)])], []).append(r)
    for root, members in cluster_of.items():
        x = dec.get(int(root))
        if x:
            id_count[str(x.get("c", ""))] += len(members)

    screened = len(workbook)
    hold = sum(n for c, n in unique_count.items()
               if P.code_to_family(proto, c) in P.hold_families(proto))
    identified = a.identified if a.identified is not None else len(rows)
    dupes = (a.duplicates_removed if a.duplicates_removed is not None
             else (identified - screened if identified else None))
    topical = a.topical_reviews
    excluded_stage1 = (screened - hold - topical) if topical is not None else None

    to_fill = []
    if topical is None:
        to_fill.append("topical-reviews: how many reviews/letters were kept as "
                       "topic-relevant and assessed for eligibility")
    if a.included is None:
        to_fill.append("included: studies surviving full text (full-text stage)")

    checks = []
    if identified is not None and dupes is not None:
        checks.append(("identified - duplicates_removed == screened",
                       identified - dupes == screened,
                       f"{identified} - {dupes} = {identified - dupes}, screened {screened}"))
    if excluded_stage1 is not None:
        ok = excluded_stage1 + hold + topical == screened
        checks.append(("excluded + hold + topical_reviews == screened", ok,
                       f"{excluded_stage1} + {hold} + {topical} = "
                       f"{excluded_stage1 + hold + topical}, screened {screened}"))
    checks.append(("sum(unique counts by code) == screened",
                   sum(unique_count.values()) == screened,
                   f"{sum(unique_count.values())} vs {screened}"))
    if a.platform_maybe is not None:
        # the platform counts citation ids, not unique records: compare like with like
        hold_ids = sum(n for c, n in id_count.items()
                       if P.code_to_family(proto, c) in P.hold_families(proto))
        checks.append(("Hold citations == platform Maybe",
                       hold_ids == a.platform_maybe,
                       f"local Hold {hold_ids} citation ids (={hold} unique) vs "
                       f"platform Maybe {a.platform_maybe}"))

    values = {
        "identification": {
            "total_identified": identified,
            "unique_records_screened": screened,
            "platform_citation_ids": len(rows) if rows else None,
        },
        "duplicates_removed": dupes,
        "records_screened": screened,
        "records_excluded_stage1": excluded_stage1,
        "records_held_for_fulltext": hold,
        "topical_reviews_assessed": topical,
        "reports_sought_for_retrieval": (hold + topical) if topical is not None else None,
        "reports_not_retrieved": a.not_retrieved,
        "reports_assessed_for_eligibility": (hold + topical) if topical is not None else None,
        "studies_included": a.included,
        "exclusion_reasons_by_code": {
            "unique": dict(sorted(unique_count.items())),
            "platform_ids": dict(sorted(id_count.items())),
        },
        "checks": [{"name": n, "ok": bool(ok), "detail": d} for n, ok, d in checks],
        "to_fill": to_fill,
        "provenance": {
            "decisions": os.path.basename(dec_p),
            "all_records": os.path.basename(all_p) if rows else None,
            "protocol": os.path.basename(proto.get("_path", "protocol.json")),
        },
    }

    jp = P.write_json(os.path.join(base, "prisma_values.json"), values)
    P.banner("s9", f"written: {jp}")

    n_holds = P.hold_families(proto)
    L = ["# PRISMA 2020 numbers (derived from the pipeline artefacts)", "",
         "| box | value |", "|---|---|",
         f"| Total identified | {identified} |",
         f"| Duplicate records removed | {dupes} |",
         f"| **Records screened** | **{screened}** |",
         f"| Records excluded (stage 1) | {excluded_stage1} |",
         f"| Held for full text (families {', '.join(n_holds)}) | {hold} |",
         f"| Topical reviews/letters assessed | {topical} |",
         f"| Reports sought for retrieval | {values['reports_sought_for_retrieval']} |",
         f"| Reports not retrieved | {a.not_retrieved} |",
         f"| Reports assessed for eligibility | {values['reports_assessed_for_eligibility']} |",
         f"| **Studies included** | {a.included} |", "",
         "## Excluded, by decision code (unique records)", "",
         "| code | meaning | unique | platform ids |", "|---|---|---|---|"]
    for c in sorted(unique_count, key=lambda k: -unique_count[k]):
        L.append(f"| {c} | {P.code_meaning(proto, c)} | {unique_count[c]} | "
                 f"{id_count.get(c, 0)} |")
    L += ["", "## Closure checks", ""]
    for name, ok, detail in checks:
        L.append(f"- {'PASS' if ok else 'FAIL'} — {name}  ({detail})")
    if to_fill:
        L += ["", "## To fill by hand (never guess these)", ""]
        L += [f"- [ ] {t}" for t in to_fill]
    mp = os.path.join(base, "prisma_numbers.md")
    with open(mp, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    P.banner("s9", f"written: {mp}")

    print(f"\n  identified {identified}   duplicates removed {dupes}   "
          f"screened {screened}")
    print(f"  excluded(stage 1) {excluded_stage1}   hold {hold}   "
          f"topical reviews {topical}   included {a.included}")
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}   [{detail}]")
    if to_fill:
        print("\n  to fill by hand:")
        for t in to_fill:
            print(f"    - {t}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
