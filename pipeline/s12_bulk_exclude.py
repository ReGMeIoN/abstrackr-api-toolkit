"""Stage 12 -- bulk-apply one adjudicated exclusion rule, with a receipt.

Some exclusions are not per-record judgements: they follow from a property a whole
*family* of records shares. The worked example is a congenital-anomaly family
(pancreaticobiliary maljunction and its synonyms). In those studies the exposure is
the **anomaly itself**, never the exposure under review, so no record in that family
can contribute an effect estimate however good its design is. Reading each one's
full text is wasted reviewer time; excluding them silently is wasted traceability.

This stage does neither. It

  1. matches the rule against the workbooks (title + abstract),
  2. reads the platform state of every match and stops at anything already
     decided (so a human judgement is never overwritten),
  3. excludes only the records still awaiting review,
  4. tags them with a rule tag, so the batch can be found and **undone** later,
  5. writes a receipt CSV with the evidence for each exclusion.

The receipt is the point: a bulk decision that cannot be audited is not a decision,
it is a deletion.

usage
    python s12_bulk_exclude.py --dir <workdir> --project <id> \
        --pattern "pancreaticobiliary maljunction" --pattern "choledochal cyst" \
        --reason "exposure mismatch: congenital anomaly, not the exposure under review" \
        --tag rule:exposure-mismatch --out rule_family.csv [--execute]
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import protocol as P  # noqa: E402

BATCH = 500


def match_records(rows, patterns, id_column, title_only=False):
    """Return records whose title (or title+abstract) matches any pattern.

    `title_only` is the conservative mode: a keyword that appears only in an
    abstract is often a passing mention (a differential diagnosis, an exclusion
    criterion) rather than the subject of the study. Matching titles finds the
    family; matching abstracts pulls in bystanders.
    """
    rx = re.compile("|".join(f"(?:{p})" for p in patterns), re.I)
    hits = []
    for r in rows:
        title = str(r.get("title") or "")
        abstract = str(r.get("abstract") or "")
        m_t = rx.search(title)
        m_a = None if title_only else rx.search(abstract)
        if not (m_t or m_a):
            continue
        where = "title" if m_t else "abstract"
        snippet = (m_t or m_a).group(0)
        hits.append({"idx": P.as_int(r.get(id_column)),
                     "citation_id": P.as_int(r.get("citation_id")),
                     "pmid": r.get("pmid", ""), "year": r.get("year", ""),
                     "match_in": where, "matched": snippet,
                     "title": title})
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--project", type=int, required=True)
    ap.add_argument("--pattern", action="append", default=[],
                    help="regex matched against title+abstract; repeatable")
    ap.add_argument("--ids-from", default=None,
                    help="CSV of citation_id/idx to act on directly (skips matching)")
    ap.add_argument("--reason", required=True, help="why the whole family is excluded")
    ap.add_argument("--tag", default=None, help="rule tag applied to every excluded id")
    ap.add_argument("--value", type=int, default=-1, help="label value (default -1 = Exclude)")
    ap.add_argument("--out", default=None, help="receipt CSV")
    ap.add_argument("--records", default=None,
                    help="CSV holding the citation ids (default: records_all.csv)")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--title-only", action="store_true",
                    help="match titles only (conservative: an abstract mention is "
                         "often a bystander, not the study's subject)")
    ap.add_argument("--execute", action="store_true")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    idcol = P.id_column(proto)
    files = P.platform_section(proto).get("files") or {}
    records_p = P.resolve(base, a.records or files.get("records") or "records_all.csv")
    rows = P.read_csv(records_p)
    P.banner("s12", f"records: {os.path.basename(records_p)} ({len(rows)} rows)   "
                    f"patterns: {len(a.pattern)}")

    if a.ids_from:
        src = P.resolve(base, a.ids_from)
        if not os.path.exists(src):
            sys.exit(f"[!] missing {src}")
        listed = P.read_csv(src)
        hits = [{"idx": P.as_int(r.get("idx")),
                 "citation_id": P.as_int(r.get("citation_id")),
                 "pmid": r.get("pmid", ""), "year": r.get("year", ""),
                 "match_in": "list", "matched": r.get("classification", ""),
                 "title": r.get("title", "")} for r in listed]
        P.banner("s12", f"records supplied by {os.path.basename(src)}: {len(hits)}")
    else:
        if not a.pattern:
            sys.exit("[!] give --pattern or --ids-from")
        hits = match_records(rows, a.pattern, idcol, title_only=a.title_only)
    P.banner("s12", f"records matching the rule: {len(hits)}")
    by_where = {}
    for h in hits:
        by_where[h["match_in"]] = by_where.get(h["match_in"], 0) + 1
    print(f"      matched in: {by_where}")
    if a.limit:
        hits = hits[:a.limit]
        P.banner("s12", f"limited to {len(hits)}")

    from abstrackr_client import Client  # noqa: E402
    c = Client()

    # --- platform state: one full crawl beats N single lookups ---------------
    state, page, seen = {}, 0, 0
    while True:
        st, _, body = c.request("GET",
                                f"/api/projects/{a.project}/citations?page={page}")
        if st != 200 or not isinstance(body, dict):
            P.banner("s12", f"crawl stopped at page {page}: HTTP {st}")
            break
        batch = body.get("citations") or []
        for cit in batch:
            cid = cit.get("id")
            if cid is None:
                continue
            state[cid] = {"my_label": cit.get("my_label"),
                          "status": cit.get("status"),
                          "my_tags": set(cit.get("my_tags") or [])}
            seen += 1
        page += 1
        if len(batch) < 100:
            break
    P.banner("s12", f"platform crawl: {seen} citations over {page} pages")
    if not state:
        # A crawl can fail (rate limiting, a dropped connection). Falling back to
        # one probe per matched citation is cheap here -- the batch is small -- and
        # it beats acting on records whose state we never read.
        P.banner("s12", "crawl returned nothing -- probing each citation directly")
        for h in hits:
            cid = h["citation_id"]
            if cid is None:
                continue
            st, _, body = c.request("GET", f"/api/citations/{cid}/batch")
            cit = (body.get("citation") or {}) if isinstance(body, dict) else {}
            status = cit.get("status")
            tags = set()
            for t in (cit.get("tags") or []):
                tags.add(t.get("name") if isinstance(t, dict) else t)
            state[cid] = {"my_label": cit.get("my_label"), "status": status,
                          "my_tags": tags, "http": st}
        P.banner("s12", f"probed {len(state)} citation(s) directly")

    print("\n      per match:")
    todo, already, unknown = [], [], []
    for h in hits:
        cid = h["citation_id"]
        s = state.get(cid)
        if cid is None or s is None:
            unknown.append(h)
            continue
        h.update({"platform_label": s["my_label"], "platform_status": s["status"],
                  "already_tagged": bool(a.tag and a.tag in s["my_tags"])})
        # Awaiting review = the project shows it unscreened AND this account holds
        # no label. Both matter: status reflects every reviewer, while my_label can
        # simply be missing from a response and would otherwise make everything
        # look unreviewed.
        if str(s["status"]).lower() == "unscreened" and s["my_label"] is None:
            todo.append(h)
        else:
            already.append(h)
        mark = "TO-EXCLUDE" if h in todo else "already decided"
        tag_note = ""
        if a.tag:
            tag_note = f" tag={'yes' if h.get('already_tagged') else 'no'}"
        print(f"        cid={cid:<9} idx={h['idx']:<7} {mark:<15} "
              f"label={s['my_label']} status={s['status']}{tag_note} "
              f"title={h['title'][:40]!r}")

    P.banner("s12", f"awaiting review: {len(todo)}   already decided (left alone): "
                    f"{len(already)}   unreadable/skipped: {len(unknown)}")

    # --- receipt ------------------------------------------------------------
    cols = ["citation_id", "idx", "pmid", "year", "match_in", "matched", "title",
            "rule", "reason", "platform_label_before", "platform_status_before",
            "action"]
    receipt = []
    for h in hits:
        receipt.append({
            "citation_id": h.get("citation_id"), "idx": h.get("idx"),
            "pmid": h.get("pmid"), "year": h.get("year"),
            "match_in": h.get("match_in"), "matched": h.get("matched"),
            "title": h.get("title"), "rule": " | ".join(a.pattern),
            "reason": a.reason,
            "platform_label_before": h.get("platform_label"),
            "platform_status_before": h.get("platform_status"),
            "action": "Exclude" if h in todo else "left unchanged",
        })
    out = P.resolve(base, a.out or "rule_exclusions.csv")
    P.write_csv(out, receipt, cols)
    P.banner("s12", f"receipt written: {out}  ({len(receipt)} rows)")

    if not todo:
        print("\n      nothing awaiting review -- nothing to write.")
        return 0
    if not a.execute:
        print(f"\n      (dry run) would label {len(todo)} citations value={a.value} "
              f"and tag them {a.tag!r}. add --execute")
        return 0

    ids = [h["citation_id"] for h in todo]
    log = os.path.join(base, f"rule_audit_{a.project}.log")
    for i in range(0, len(ids), BATCH):
        chunk = ids[i:i + BATCH]
        st, _, body = c.request("POST", f"/api/projects/{a.project}/bulk-labels",
                                {"citation_ids": chunk, "value": a.value})
        print(f"      bulk-labels n={len(chunk)} -> {st} "
              f"{'' if st in (200, 201) else str(body)[:160]}")
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(f"bulk-labels value={a.value} n={len(chunk)} status={st} "
                     f"reason={a.reason}\n")
        if a.tag and st in (200, 201):
            st2, _, body2 = c.request("POST", f"/api/projects/{a.project}/bulk-tags",
                                      {"citation_ids": chunk, "tag": a.tag,
                                       "action": "add"})
            print(f"      bulk-tags {a.tag} n={len(chunk)} -> {st2} "
                  f"{'' if st2 in (200, 201) else str(body2)[:160]}")
            with open(log, "a", encoding="utf-8") as fh:
                fh.write(f"bulk-tags {a.tag} n={len(chunk)} status={st2}\n")

    st, _, prog = c.request("GET", f"/api/projects/{a.project}/progress")
    P.banner("s12", f"progress now: {prog}")
    P.banner("s12", f"audit log: {log}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
