"""Stage 7 -- push a title/abstract pass into an abstrackr project (labels + tags).

This is the only stage that writes to the platform. It is built around the one
structural fact that makes a review pool messy: **the platform holds redundant
copies** (the cholecystectomy project had 9,438 citations for 8,491 unique
records). Decisions are taken once per unique record and then **fanned out to
every citation id in the same duplicate cluster**, so the platform can never show
two sibling copies with two different verdicts.

The cluster representative is the *smallest* id in the cluster -- the same row the
de-duplication stage kept -- so local decisions and platform ids line up exactly.
That invariant is shared code (`protocol.cluster_roots`), not a convention.

Safety
    * dry run by default; every write needs --execute
    * refuses to run at all if any unique record has no decision: a partial pass
      must never reach the platform
    * resumable -- a state file records which citation ids already carry a label,
      so an interrupted run continues instead of double-writing
    * one request at a time, ~1 s apart, appending to an audit log
    * labels only: the protocol maps each family to a label value. The
      title/abstract stage of a review with a Hold pool maps Hold families to
      Maybe (0) and never writes Include (1)

usage
    python s7_submit.py --dir <workdir> --project 5691 [--protocol p.json]
    python s7_submit.py --dir <workdir> --project 5691 --execute
"""

import argparse
import os
import sys
import time
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol as P  # noqa: E402


def build_write_plan(proto, base, rows, dec, meta, tag_prefix=None):
    """Return (plan, clusters) -- one plan entry per platform citation id.

    Shared with s8_verify.py on purpose: if the writer and the verifier computed
    the expectation separately, agreement between them would prove nothing.
    """
    idcol = P.id_column(proto)
    plat = P.platform_section(proto)
    prefix = tag_prefix or plat.get("tag_prefix", "AI")
    roots = P.cluster_roots(rows, idcol, P.dedup_keys(proto))

    clusters = {}
    for r in rows:
        clusters.setdefault(roots[str(r[idcol])], []).append(r)

    missing = [k for k in clusters if int(k) not in dec]
    if missing:
        raise SystemExit(
            f"!! {len(missing)} unique records have no decision, e.g. {sorted(missing)[:5]}\n"
            f"   refusing to submit a partial pass; finish those batches first.")

    plan = []
    for root, members in clusters.items():
        d = dec[int(root)]
        family = str(d.get("d", "")).upper()[:1]
        value = P.label_value(proto, family)
        if value is None:
            raise SystemExit(f"!! family {family!r} has no platform label value "
                             f"(protocol.platform.value_by_family)")
        is_copy = len(members) > 1
        for m in members:
            tags = P.build_tags(proto, d, root, meta.get(int(root)),
                                is_dup_copy=is_copy and str(m[idcol]) != str(root))
            plan.append({"idx": str(m[idcol]),
                         "citation_id": int(m["citation_id"]),
                         "value": value, "tags": tags, "root": root,
                         "family": family, "code": d.get("c", "")})
    plan.sort(key=lambda p: p["citation_id"])
    return plan, clusters


def summarize(proto, plan):
    dist = Counter(p["family"] for p in plan)
    values = Counter(p["value"] for p in plan)
    tags = Counter(t for p in plan for t in p["tags"])
    print(f"  write plan: {len(plan)} citation ids "
          f"({len({p['root'] for p in plan})} unique records)")
    for f, n in sorted(dist.items()):
        print(f"    family {f}: {n}  -> label {P.label_value(proto, f)} "
              f"({P.label_name(proto, P.label_value(proto, f))})")
    print(f"    labels by value: {dict(values)}")
    if any(v == 1 for v in values):
        print("    !! Include(1) is being written -- a title/abstract stage with a "
              "Hold pool should never do this")
    print(f"  distinct tags: {len(tags)}   tag writes: {sum(tags.values())}")
    for t, n in tags.most_common():
        print(f"    {t:<34} {n}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--records", default=None, help="ALL rows with citation_id")
    ap.add_argument("--decisions", default=None, help="record id -> decision map")
    ap.add_argument("--enriched", default=None, help="workbook used for meta: tags")
    ap.add_argument("--tag-prefix", default=None)
    ap.add_argument("--no-group-tags", action="store_true",
                    help="write only <prefix>:<code>, skip form/exp/out and role tags")
    ap.add_argument("--batch", type=int, default=500)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    plat = P.platform_section(proto)
    files = plat.get("files") or {}
    records_p = P.resolve(base, a.records or files.get("records") or "records_all.csv")
    dec_p = P.resolve(base, a.decisions or files.get("decisions")
                      or "all_decisions_normalized.json")
    enriched_p = P.resolve(base, a.enriched or files.get("enriched")
                           or P.workbook_spec(proto).get("records"))

    for p in (records_p, dec_p):
        if not os.path.exists(p):
            P.banner("s7", f"missing input: {p}")
            return 2

    rows = P.read_csv(records_p)
    dec = {int(k): v for k, v in P.read_json(dec_p).items()}
    P.banner("s7", f"pool rows: {len(rows)}   unique decisions: {len(dec)}")

    meta = {}
    if os.path.exists(enriched_p):
        idcol = P.id_column(proto)
        for r in P.read_csv(enriched_p):
            meta[P.as_int(r.get(idcol))] = r
        P.banner("s7", f"workbook metadata: {len(meta)} rows ({os.path.basename(enriched_p)})")
    else:
        P.banner("s7", f"no workbook at {enriched_p}; meta tags will be skipped")

    plan, clusters = build_write_plan(proto, base, rows, dec, meta, a.tag_prefix)
    if a.no_group_tags:
        keep = {f"{plat.get('tag_prefix', 'AI')}:"}
        for p in plan:
            p["tags"] = [t for t in p["tags"]
                         if any(t.startswith(k) for k in keep)]
    print(f"  clusters: {len(clusters)}  (singletons "
          f"{sum(1 for v in clusters.values() if len(v) == 1)})")
    summarize(proto, plan)

    state_p = os.path.join(base, f"submit_state_{a.project}.json")
    log_p = os.path.join(base, f"submit_audit_{a.project}.log")
    state = {"labeled": [], "tagged": []}
    if os.path.exists(state_p):
        state = P.read_json(state_p)
    done_label = set(state.get("labeled", []))
    todo = [p for p in plan if p["citation_id"] not in done_label]
    if a.limit:
        todo = todo[:a.limit]
    P.banner("s7", f"already labeled: {len(done_label)}   to do now: {len(todo)}")
    P.banner("s7", f"state -> {state_p}\n            audit -> {log_p}")

    if not a.execute:
        print("\n  (dry run) add --execute to write to the platform.")
        return 0

    from abstrackr_client import Client  # noqa: E402
    c = Client()
    st, _, me = c.request("GET", "/api/user")
    if st != 200:
        P.banner("s7", f"GET /api/user -> {st}. Run 'python abstrackr_client.py login' first.")
        return 2
    uid = me.get("id") if isinstance(me, dict) else me
    P.banner("s7", f"writing as user {uid} into project {a.project}")

    def audit(line):
        with open(log_p, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")

    by_value = {}
    for p in todo:
        by_value.setdefault(p["value"], []).append(p["citation_id"])
    for value, ids in sorted(by_value.items()):
        for i in range(0, len(ids), a.batch):
            chunk = ids[i:i + a.batch]
            st, _, body = c.request("POST", f"/api/projects/{a.project}/bulk-labels",
                                    {"citation_ids": chunk, "value": value})
            ok = st in (200, 201)
            print(f"  bulk-labels value={value} n={len(chunk)} -> {st} "
                  f"{'' if ok else str(body)[:160]}")
            audit(f"bulk-labels value={value} n={len(chunk)} status={st}")
            if ok:
                state["labeled"] = sorted(set(state["labeled"]) | set(chunk))
                P.write_json(state_p, state)

    by_tag = {}
    for p in todo:
        for t in p["tags"]:
            by_tag.setdefault(t, []).append(p["citation_id"])
    for tag, ids in sorted(by_tag.items()):
        for i in range(0, len(ids), a.batch):
            chunk = ids[i:i + a.batch]
            st, _, body = c.request("POST", f"/api/projects/{a.project}/bulk-tags",
                                    {"citation_ids": chunk, "tag": tag, "action": "add"})
            ok = st in (200, 201)
            print(f"  bulk-tags {tag:<34} n={len(chunk):<5} -> {st} "
                  f"{'' if ok else str(body)[:160]}")
            audit(f"bulk-tags {tag} n={len(chunk)} status={st}")
            if ok:
                state["tagged"] = sorted(set(state["tagged"]) | set(chunk))
                P.write_json(state_p, state)

    P.banner("s7", f"labels written: {len(state['labeled'])} / {len(plan)}")
    P.banner("s7", "now run s8_verify.py -- it crawls every citation back and "
                   "compares tag sets one by one")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
