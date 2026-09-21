"""Stage 11 -- release AI-written labels so a human can actually screen.

The problem this exists for
---------------------------
The pipeline submits its decisions through **one** account. That account then
counts as having screened everything, so every queue for it comes back empty:

    progress.my_labeled == total          the account looks finished
    GET /next                 -> null     no normal queue
    screening set on {"labeled_by_decision":"maybe"}
                              -> done == total, "nothing left to screen"

A human review that never appears in the platform is not a human review, and a
reviewer who cannot reach the records cannot do one. The fix is to move the AI
verdict out of the *label* and into a *tag*, delete the label, and rebuild the
queue on the tag:

    label  my_label = 0 (Maybe)     -> what the human must now decide
    tags   AI-p1:F1, hold:fulltext  -> the AI verdict, kept and traceable

After a release the reviewer starts from `unscreened` and screens for real, while
the AI verdict stays fully auditable. `--restore` puts the labels back exactly as
they were, so the operation is reversible.

Also note: a screening set is a **live filter**. A set built on
`{"labeled_by_decision":"maybe"}` becomes EMPTY the moment you release the labels
-- which is why this stage builds its set on a **tag** instead.

Safety
    * dry run by default; every write needs --execute
    * the citation ids come from a platform filter or a local CSV -- never invented
    * `--expect N` refuses to act when the id set is not the size you think it is
      (an ignored platform filter would otherwise return the whole project)

usage
    python s11_release_labels.py --dir <workdir> --project 5691 --inspect
    python s11_release_labels.py --dir <workdir> --project 5691 --tag hold:fulltext
    python s11_release_labels.py --dir <workdir> --project 5691 --tag hold:fulltext --release --execute
    python s11_release_labels.py --dir <workdir> --project 5691 --ids-from queue.csv --release --execute
    python s11_release_labels.py --dir <workdir> --project 5691 --tag hold:fulltext --restore --execute
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol as P  # noqa: E402
from s10_export_queue import crawl, label_name  # noqa: E402

CHUNK = 1000


def collect_ids(client, project, tag, ids_from, max_pages=0):
    if ids_from:
        rows = P.read_csv(ids_from)
        key = "citation_id" if rows and "citation_id" in rows[0] else None
        if not key:
            raise SystemExit(f"[!] {ids_from} has no 'citation_id' column")
        ids = sorted({P.as_int(r[key]) for r in rows if P.as_int(r[key]) is not None})
        print(f"  ids from {os.path.basename(ids_from)}: {len(ids)}")
        return ids
    print(f"  crawling the platform with filter {{'tag': {tag!r}}} ...")
    rows = crawl(client, project, {"tag": tag}, max_pages)
    return sorted({c["id"] for c in rows if c.get("id") is not None})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--tag", default="hold:fulltext",
                    help="tag whose citations should be released (default hold:fulltext)")
    ap.add_argument("--ids-from", default=None, help="local CSV with a citation_id column")
    ap.add_argument("--expect", type=int, default=None)
    ap.add_argument("--set-name", default=None, help="name for --create-set")
    ap.add_argument("--inspect", action="store_true")
    ap.add_argument("--create-set", action="store_true")
    ap.add_argument("--delete-set", type=int, default=None, metavar="SET_ID")
    ap.add_argument("--release", action="store_true",
                    help="delete the label so the reviewer starts from unscreened")
    ap.add_argument("--restore", action="store_true",
                    help="put the Maybe(0) label back")
    ap.add_argument("--value", type=int, default=0,
                    help="label value to write with --restore (0 = Maybe)")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    from abstrackr_client import Client  # noqa: E402
    c = Client()

    if a.inspect:
        st, _, prog = c.request("GET", f"/api/projects/{a.project}/progress")
        st2, _, sets = c.request("GET", f"/api/projects/{a.project}/screening_sets")
        st3, _, nxt = c.request("GET", f"/api/projects/{a.project}/next")
        st4, _, con = c.request("GET", f"/api/projects/{a.project}/next?mode=conflict")
        st5, _, cc = c.request("GET", f"/api/projects/{a.project}/conflict_count")
        print(f"  progress      : {prog}")
        print(f"  next          : {nxt}")
        print(f"  next(conflict): {con}")
        print(f"  conflict_count: {cc}")
        if isinstance(sets, list):
            print(f"  screening sets: {len(sets)}")
            for s in sets:
                print(f"    id={s.get('id')} total={s.get('total')} done={s.get('done')}"
                      f"/{s.get('my_done')} filter={s.get('filter_json')} "
                      f"name={s.get('name')!r}")
        return 0

    if a.delete_set is not None:
        if not a.execute:
            print(f"  (dry run) would DELETE screening set {a.delete_set}")
            return 0
        st, _, body = c.request("DELETE", f"/api/projects/{a.project}/screening_sets/"
                                           f"{a.delete_set}")
        print(f"  DELETE set {a.delete_set} -> {st} {str(body)[:160]}")
        return 0

    if a.create_set:
        name = a.set_name or f"AI release · {a.tag} · needs human review"
        payload = {"name": name, "filter_json": {"tag": a.tag}}
        if not a.execute:
            print(f"  (dry run) would POST /screening_sets {payload}")
            return 0
        st, _, body = c.request("POST", f"/api/projects/{a.project}/screening_sets", payload)
        print(f"  POST screening_sets -> {st} {str(body)[:300]}")
        if st not in (200, 201):
            return 2
        sid = (body or {}).get("id")
        st2, _, sets = c.request("GET", f"/api/projects/{a.project}/screening_sets")
        made = next((s for s in (sets or []) if s.get("id") == sid), {})
        total = made.get("total")
        print(f"  new set {sid}: total={total} filter={made.get('filter_json')}")
        if a.expect is not None and total != a.expect:
            print(f"  !! total {total} != expected {a.expect} -- the platform may ignore "
                  f"filter_json; deleting the set again")
            c.request("DELETE", f"/api/projects/{a.project}/screening_sets/{sid}")
            return 2
        return 0

    if not (a.release or a.restore):
        P.banner("s11", "give one of --inspect / --create-set / --release / --restore "
                        "/ --delete-set")
        return 2

    ids = collect_ids(c, a.project, a.tag, P.resolve(os.path.abspath(a.dir), a.ids_from)
                      if a.ids_from else None, a.max_pages)
    P.banner("s11", f"citation ids in scope: {len(ids)}")
    if len(ids) >= 9000:
        P.banner("s11", "!! that is the whole project -- a filter was probably ignored; "
                        "refusing to act on it")
        return 2
    if a.expect is not None and len(ids) != a.expect:
        P.banner("s11", f"!! expected {a.expect} ids, got {len(ids)} -- refusing")
        return 2

    action = "release (DELETE label -> unscreened)" if a.release \
        else f"restore (label -> {a.value} = {label_name(proto, a.value)})"
    P.banner("s11", f"would {action} for {len(ids)} citations")
    if not a.execute:
        print("  (dry run) add --execute to write")
        return 0

    ok = 0
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        if a.release:
            st, _, body = c.request("DELETE", f"/api/projects/{a.project}/bulk-labels",
                                    {"citation_ids": chunk})
        else:
            st, _, body = c.request("POST", f"/api/projects/{a.project}/bulk-labels",
                                    {"citation_ids": chunk, "value": a.value})
        good = st in (200, 201)
        ok += good
        print(f"  {'DELETE' if a.release else 'POST'} bulk-labels n={len(chunk)} -> {st} "
              f"{'' if good else str(body)[:200]}")
    if ok:
        st, _, prog = c.request("GET", f"/api/projects/{a.project}/progress")
        P.banner("s11", f"progress now: {prog}")
        st, _, nxt = c.request("GET", f"/api/projects/{a.project}/next")
        P.banner("s11", f"normal queue now: {nxt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
