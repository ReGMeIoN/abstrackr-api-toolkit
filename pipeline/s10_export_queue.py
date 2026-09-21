"""Stage 10 -- export a platform screening queue (a screening set) and cross-check it.

An abstrackr **screening set** is a *saved filter*, not a hand-curated list:

    {"labeled_by_decision": "maybe"}   -> every citation you labelled Maybe
    {"tag": "hold:fulltext"}           -> every citation carrying that tag

The platform re-evaluates the filter live, so a set is the right way to say "give
me the 258 Maybe records and nothing else" -- no re-upload, no separate project,
and it keeps tracking as labels change.

This stage brings such a queue back down to a local CSV/JSONL a human can work
through offline, and **cross-checks it against the local decision map while doing
so**: a queue the reviewer trusts must be one whose size and contents have been
verified, not one that merely looked right in the browser.

Safety: read-only against the platform. `--expect N` turns a silently-ignored
filter (which would return the whole project) into a loud failure instead of a
9,000-row file nobody notices.

usage
    python s10_export_queue.py --dir <workdir> --project 5691 --list-sets
    python s10_export_queue.py --dir <workdir> --project 5691 --set-id 802
    python s10_export_queue.py --dir <workdir> --project 5691 --labeled-by-decision maybe
    python s10_export_queue.py --dir <workdir> --project 5691 --set-id 802 --expect 258
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol as P  # noqa: E402

PAGE_SIZE = 100


def crawl(client, project, params, max_pages=0, label=""):
    """Page through the citation list until a short page; de-duplicate by id."""
    seen, rows, page = set(), [], 0
    while True:
        q = f"/api/projects/{project}/citations?page={page}"
        for k, v in params.items():
            q += f"&{k}={v}"
        st, _, body = client.request("GET", q)
        if st != 200 or not isinstance(body, dict):
            P.banner("s10", f"page {page} failed: HTTP {st} {str(body)[:160]}")
            break
        batch = body.get("citations") or []
        new = 0
        for cit in batch:
            cid = cit.get("id")
            if cid is None or cid in seen:
                continue
            seen.add(cid)
            rows.append(cit)
            new += 1
        print(f"  page {page:<4} rows {len(batch):<4} new {new:<4} total {len(rows)}")
        if len(batch) < PAGE_SIZE:
            break
        page += 1
        if max_pages and page >= max_pages:
            break
    return rows


def label_name(proto, value):
    names = {"1": "Include", "0": "Maybe", "-1": "Exclude", "None": "unscreened"}
    return names.get(str(value), P.label_name(proto, value))


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--set-id", type=int, default=None)
    ap.add_argument("--labeled-by-decision", default=None,
                    choices=["include", "exclude", "maybe"])
    ap.add_argument("--tag", default=None)
    ap.add_argument("--status", default=None)
    ap.add_argument("--list-sets", action="store_true")
    ap.add_argument("--expect", type=int, default=None,
                    help="fail loudly if the queue size differs from this")
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)

    from abstrackr_client import Client  # noqa: E402
    c = Client()

    if a.list_sets:
        st, _, body = c.request("GET", f"/api/projects/{a.project}/screening_sets")
        if st != 200 or not isinstance(body, list):
            P.banner("s10", f"could not list sets: HTTP {st} {str(body)[:160]}")
            return 2
        print(f"  {len(body)} screening set(s) on project {a.project}:")
        for s in body:
            print(f"    id={s.get('id'):<6} total={s.get('total'):<6} "
                  f"done={s.get('done')}/{s.get('my_done')}  "
                  f"filter={s.get('filter_json')}  name={s.get('name')!r}  "
                  f"created={s.get('created_at')} by {s.get('creator_email')}")
        return 0

    params = {}
    if a.set_id is not None:
        params["set_id"] = a.set_id
    if a.labeled_by_decision:
        params["labeled_by_decision"] = a.labeled_by_decision
    if a.tag:
        params["tag"] = a.tag
    if a.status:
        params["status"] = a.status
    if not params:
        P.banner("s10", "give one of --set-id / --labeled-by-decision / --tag / --status "
                        "(or --list-sets)")
        return 2

    P.banner("s10", f"crawling project {a.project} with filter {params}")
    rows = crawl(c, a.project, params, a.max_pages)
    P.banner("s10", f"queue size: {len(rows)}")

    if a.expect is not None and len(rows) != a.expect:
        P.banner("s10", f"!! expected {a.expect} but got {len(rows)} -- treat this queue "
                        f"as UNVERIFIED (a filter may have been ignored)")
    if a.set_id is not None and len(rows) >= 9000:
        P.banner("s10", "!! this looks like the whole project -- the filter was probably "
                        "ignored, not honoured")

    # --- cross-check against the local decision map -------------------------
    all_p = P.resolve(base, (P.platform_section(proto).get("files") or {})
                      .get("records") or "records_all.csv")
    dec_p = P.resolve(base, (P.platform_section(proto).get("files") or {})
                      .get("decisions") or "all_decisions_normalized.json")
    by_cid, cid_to_idx, all_rows = {}, {}, []
    if os.path.exists(all_p):
        all_rows = P.read_csv(all_p)
        for r in all_rows:
            cid = P.as_int(r.get("citation_id"))
            if cid is not None:
                by_cid[cid] = r
                cid_to_idx[cid] = P.as_int(r.get(P.id_column(proto)))
    dec = {}
    if os.path.exists(dec_p):
        dec = {int(k): v for k, v in P.read_json(dec_p).items()}
    # A platform citation id may be a DUPLICATE COPY: its own workbook idx then has
    # no decision, because decisions are taken once per unique record. Map every idx
    # to its cluster representative (same code s7/s8 use) before looking one up --
    # otherwise a queue of 258 Maybe citations shows 47 records with no code at all.
    roots = (P.cluster_roots(all_rows, P.id_column(proto), P.dedup_keys(proto))
             if all_rows else {})
    if roots:
        n_copies = sum(1 for k, v in roots.items() if str(k) != str(v))
        print(f"  workbook: {len(all_rows)} citation ids, "
              f"{len(set(roots.values()))} unique records, {n_copies} duplicate copies")

    out_rows, unknown = [], []
    for cit in rows:
        cid = cit.get("id")
        row = by_cid.get(cid)
        i = cid_to_idx.get(cid)
        root = roots.get(str(i), str(i)) if i is not None else None
        x = dec.get(P.as_int(root), {}) if root is not None else {}
        if row is None:
            unknown.append(cid)
        tags = [t for t in (cit.get("my_tags") or [])]
        out_rows.append({
            "citation_id": cid,
            "idx": i if i is not None else "",
            "root_idx": P.as_int(root) if root is not None else "",
            "platform_label": cit.get("my_label"),
            "platform_status": cit.get("status"),
            "c": x.get("c", ""),
            "d": x.get("d", ""),
            "form": x.get("form", ""),
            "exp": x.get("exp", ""),
            "out": x.get("out", ""),
            "need": "|".join(x.get("need") or []) if isinstance(x.get("need"), list)
                    else str(x.get("need") or ""),
            "reason": x.get("r", ""),
            "eff": x.get("eff", ""),
            "pmid": (row or {}).get("pmid", ""),
            "year": (row or {}).get("year", ""),
            "language": (row or {}).get("language", ""),
            "has_abstract": (row or {}).get("has_abstract", ""),
            "tags": "|".join(sorted(tags)),
            "title": (row or {}).get("title", cit.get("title", "")),
        })
    out_rows.sort(key=lambda r: (str(r["c"]), str(r["root_idx"]), r["citation_id"]))

    labels = Counter(str(r["platform_label"]) for r in out_rows)
    codes = Counter(r["c"] for r in out_rows)                 # per citation id
    codes_u = Counter(r["c"] for r in out_rows
                      if str(r["idx"]) == str(r["root_idx"]))  # per unique record
    unique_roots = {r["root_idx"] for r in out_rows if r["root_idx"] != ""}
    print(f"\n  platform labels: " + ", ".join(
        f"{label_name(proto, k)}={v}" for k, v in sorted(labels.items())))
    print(f"  citation ids: {len(out_rows)}   unique records: {len(unique_roots)}")
    print(f"  local codes (citation ids): " + ", ".join(
        f"{k}={v}" for k, v in codes.most_common()))
    print(f"  local codes (unique): " + ", ".join(
        f"{k}={v}" for k, v in codes_u.most_common()))
    if unknown:
        print(f"  !! {len(unknown)} citation ids are not in the local workbook: {unknown[:5]}")

    cols = ["citation_id", "idx", "root_idx", "platform_label", "platform_status",
            "d", "c", "form", "exp", "out", "need", "reason", "eff", "pmid", "year",
            "language", "has_abstract", "tags", "title"]
    if a.set_id is not None:
        default_name = f"queue_set{a.set_id}.csv"
    elif a.labeled_by_decision:
        default_name = f"queue_{a.labeled_by_decision}.csv"
    elif a.tag:
        default_name = f"queue_tag_{a.tag.replace(':', '-')}.csv"
    else:
        default_name = "queue.csv"
    out = P.resolve(base, a.out or default_name)
    P.write_csv(out, out_rows, cols)
    P.banner("s10", f"written: {out}   ({len(out_rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
