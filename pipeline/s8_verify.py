"""Stage 8 -- verify the platform write by crawling every citation back.

A write is not done when the API returns 200; it is done when the platform can be
shown to agree with the local expectation. abstrackr has **no tag-filter
endpoint** (`citations?tag=` is silently ignored), so the only honest check is to
crawl the whole project and read each citation's `my_tags` and `my_label`.

That is what this stage does, and it reports four kinds of disagreement:

    missing tags          expected locally, absent on the platform
    count mismatches      tag present but on the wrong number of citations
    unexpected tags       on the platform but not in the local plan
    label mismatches      per citation, expected value vs `my_label`
plus per-citation tag-set mismatches, which catch a tag landing on the wrong copy
of a duplicate pair -- something aggregate counts hide.

The expectation is built by importing s7's plan builder, so writer and verifier
cannot drift apart.

Note: the listing endpoint is 0-indexed, 100 rows per page, and `limit`/`offset`/
`per_page` are ignored. Paging is therefore sequential from 0 and stops on a
short page -- do not compute pages from a count.

usage
    python s8_verify.py --dir <workdir> --project 5691 [--protocol p.json]
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol as P  # noqa: E402
from s7_submit import build_write_plan  # noqa: E402

PAGE_SIZE = 100


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--records", default=None)
    ap.add_argument("--decisions", default=None)
    ap.add_argument("--enriched", default=None)
    ap.add_argument("--tag-prefix", default=None)
    ap.add_argument("--max-pages", type=int, default=0, help="0 = until a short page")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    files = P.platform_section(proto).get("files") or {}
    records_p = P.resolve(base, a.records or files.get("records") or "records_all.csv")
    dec_p = P.resolve(base, a.decisions or files.get("decisions")
                      or "all_decisions_normalized.json")
    enriched_p = P.resolve(base, a.enriched or files.get("enriched")
                           or P.workbook_spec(proto).get("records"))

    rows = P.read_csv(records_p)
    dec = {int(k): v for k, v in P.read_json(dec_p).items()}
    idcol = P.id_column(proto)
    meta = {}
    if os.path.exists(enriched_p):
        for r in P.read_csv(enriched_p):
            meta[P.as_int(r.get(idcol))] = r

    plan, _ = build_write_plan(proto, base, rows, dec, meta, a.tag_prefix)
    expect_tags = {p["citation_id"]: set(p["tags"]) for p in plan}
    expect_labels = {p["citation_id"]: p["value"] for p in plan}
    agg = Counter(t for p in plan for t in p["tags"])
    P.banner("s8", f"expectation: {len(expect_tags)} citation ids, {len(agg)} distinct "
                   f"tags, {sum(agg.values())} tag placements")

    from abstrackr_client import Client  # noqa: E402
    c = Client()

    got_agg, got_label, mismatches, seen = Counter(), {}, [], set()
    page, pages = 0, 0
    while True:
        st, _, body = c.request(
            "GET", f"/api/projects/{a.project}/citations?page={page}")
        if st != 200 or not isinstance(body, dict):
            P.banner("s8", f"page {page} failed: HTTP {st} {str(body)[:160]}")
            break
        batch = body.get("citations") or []
        pages += 1
        for cit in batch:
            cid = cit.get("id")
            if cid is None:
                continue
            tags = set(cit.get("my_tags") or [])
            got_agg.update(tags)
            got_label[cid] = cit.get("my_label")
            seen.add(cid)
            if cid in expect_tags and tags != expect_tags[cid]:
                mismatches.append((cid, sorted(expect_tags[cid] - tags)[:4],
                                   sorted(tags - expect_tags[cid])[:4]))
        if not a.max_pages and len(batch) < PAGE_SIZE:
            break
        page += 1
        if a.max_pages and page >= a.max_pages:
            break
    P.banner("s8", f"crawled {len(seen)} citations over {pages} pages "
                   f"(ids seen: {len(seen)}/{len(expect_tags)} expected)")

    missing = {k: v for k, v in agg.items() if k not in got_agg}
    diff = {k: (v, got_agg[k]) for k, v in agg.items()
            if k in got_agg and got_agg[k] != v}
    extra = {k: v for k, v in got_agg.items() if k not in agg}
    bad_label = [(k, v, got_label.get(k)) for k, v in expect_labels.items()
                 if k in seen and got_label.get(k) != v]

    print(f"\n  tags: matched {len(agg) - len(missing) - len(diff)}   "
          f"missing {len(missing)}   count-mismatch {len(diff)}   unexpected {len(extra)}")
    for k, v in list(missing.items())[:10]:
        print(f"    MISSING {k} (expected on {v} citations)")
    for k, (e, g) in list(diff.items())[:10]:
        print(f"    MISMATCH {k}: expected {e}, platform {g}")
    for k, v in list(extra.items())[:10]:
        print(f"    UNEXPECTED {k} = {v}")
    print(f"  labels: mismatch {len(bad_label)}")
    for k, e, g in bad_label[:10]:
        print(f"    cid={k} expected={e} got={g}")
    print(f"  per-citation tag-set mismatches: {len(mismatches)}")
    for cid, miss, ext in mismatches[:10]:
        print(f"    cid={cid} missing={miss} extra={ext}")

    ok = (not missing and not diff and not extra and not bad_label and not mismatches
          and len(seen) >= len(expect_tags))
    print("\n  RESULT:", "PLATFORM WRITE FULLY VERIFIED (labels + tags)"
          if ok else "DISCREPANCY FOUND -- do not report this pass as clean")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
