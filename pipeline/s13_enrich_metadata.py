"""Stage 13 -- enrich records that arrived without an abstract.

Screening is abstract screening. A record with only a title cannot be judged
honestly, and a review queue full of them cannot be worked through. The usual cause
is an import that dropped the abstract (and often the DOI and the authors too);
the cure is to go and get them back.

Two routes, tried in order:

  1. the record has a PMID          -> efetch it from PubMed
  2. it has only a title            -> esearch the title, then efetch the best hit,
                                       and **verify the returning title matches**
                                       before accepting anything

Nothing is accepted on faith: a title search that lands on a different paper is far
worse than a blank field, because the reviewer would then judge the wrong study.
Every accepted match is recorded with its method and its title check in the patch
file, and everything unmatched is listed as unresolved.

NCBI E-utilities allow 3 requests/second without a key; the system proxy is
deliberately bypassed (it breaks TLS to NCBI intermittently on this machine).

usage
    python s13_enrich_metadata.py --dir <workdir> --from-csv worklist_maybe.csv \
        --out maybe_metadata_patch.csv [--limit N]
"""

import argparse
import os
import re
import sys
import time
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
TOOL = {"tool": "dsh-screening-pipeline", "email": "research@example.org"}
SLEEP = 0.4


def _get(path, params):
    url = f"{EUTILS}/{path}?" + urllib.parse.urlencode({**params, **TOOL})
    for attempt in range(4):
        try:
            with OPENER.open(url, timeout=60) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                print(f"      ! {path} failed: {type(exc).__name__}: {exc}")
                return ""
            time.sleep(3)
    return ""


def parse_medline(text):
    """MEDLINE -> {pmid: {abstract, doi, authors, journal, year, title}}."""
    out = {}
    for block in re.split(r"\n\s*\n", text):
        if "PMID- " not in block:
            continue
        rec = {"abstract": "", "doi": "", "authors": "", "journal": "", "year": "",
               "title": ""}
        pmid = None
        field = None
        for line in block.splitlines():
            m = re.match(r"^([A-Z]{2,4})\s*- (.*)$", line)
            if m:
                tag, val = m.group(1), m.group(2).strip()
                field = tag
                if tag == "PMID":
                    pmid = val
                elif tag == "AB":
                    rec["abstract"] = (rec["abstract"] + " " + val).strip()
                elif tag == "TI":
                    rec["title"] = (rec["title"] + " " + val).strip()
                elif tag in ("FAU", "AU"):
                    if tag == "AU":
                        rec["authors"] = (rec["authors"] + ", " + val).strip(", ")
                elif tag in ("JT", "TA") and not rec["journal"]:
                    rec["journal"] = val
                elif tag == "DP" and not rec["year"]:
                    y = re.match(r"(\d{4})", val)
                    rec["year"] = y.group(1) if y else ""
                elif tag in ("LID", "AID") and "[doi]" in val.lower():
                    rec["doi"] = val.split()[0]
            elif line.startswith("      ") and field in ("AB", "TI"):
                rec["abstract" if field == "AB" else "title"] += " " + line.strip()
        if pmid:
            for k in rec:
                rec[k] = re.sub(r"\s+", " ", rec[k]).strip()
            out[pmid] = rec
    return out


def efetch(pmids):
    if not pmids:
        return {}
    text = _get("efetch.fcgi", {"db": "pubmed", "retmode": "text",
                                "rettype": "medline", "id": ",".join(pmids)})
    time.sleep(SLEEP)
    return parse_medline(text)


def norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())


def title_matches(a, b):
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    short, long_ = sorted((na, nb), key=len)
    return len(short) >= 25 and short in long_


def esearch_title(title):
    clean = re.sub(r"[\[\]\"']", " ", title).strip()
    if len(clean) < 12:
        return []
    text = _get("esearch.fcgi", {"db": "pubmed", "retmode": "json", "retmax": 3,
                                 "term": f'"{clean}"[Title]'})
    time.sleep(SLEEP)
    try:
        import json
        return json.loads(text)["esearchresult"].get("idlist", [])
    except Exception:  # noqa: BLE001
        return []


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--from-csv", default="worklist_maybe.csv",
                    help="CSV with an idx column and a has_abstract column")
    ap.add_argument("--out", default="maybe_metadata_patch.csv")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    src = P.resolve(base, a.from_csv)
    if not os.path.exists(src):
        sys.exit(f"[!] missing {src}")
    rows = P.read_csv(src)
    wb = {str(r.get(P.id_column(proto))): r for r in P.load_workbook(proto, base)}

    todo = [r for r in rows
            if str(r.get("has_abstract", "")).strip().upper().startswith("NO")]
    if a.limit:
        todo = todo[:a.limit]
    print(f"[s13] candidates without an abstract: {len(todo)}")

    with_pmid = [r for r in todo if str(r.get("pmid") or "").strip()]
    without = [r for r in todo if not str(r.get("pmid") or "").strip()]
    print(f"      with a PMID (direct efetch): {len(with_pmid)}")
    print(f"      title-only (esearch + verify): {len(without)}\n")

    patch = []

    def record(hit, r, method, pmid, matched):
        meta = hit or {}
        patch.append({
            "idx": r.get("idx"), "citation_id": r.get("citation_id"),
            "method": method, "pmid": pmid,
            "title_match": "exact" if matched == "exact" else
                           ("prefix/substring" if matched else "NO MATCH"),
            "abstract_chars": len(meta.get("abstract", "")),
            "doi": meta.get("doi", ""), "authors": meta.get("authors", "")[:80],
            "journal": meta.get("journal", ""), "year": meta.get("year", ""),
            "pubmed_title": meta.get("title", "")[:110],
            "abstract": meta.get("abstract", ""),
        })

    # route 1: straight fetch by PMID
    pms = [str(r["pmid"]).strip() for r in with_pmid]
    print(f"      fetching {len(pms)} PMID(s) ...")
    got = efetch(pms)
    for r in with_pmid:
        pm = str(r["pmid"]).strip()
        hit = got.get(pm)
        if hit and hit["abstract"]:
            print(f"        idx={r['idx']:<7} pmid={pm:<10} abstract "
                  f"{len(hit['abstract'])} chars")
            record(hit, r, "efetch-by-pmid", pm, True)
        else:
            print(f"        idx={r['idx']:<7} pmid={pm:<10} NO abstract in PubMed")
            record(hit, r, "efetch-by-pmid", pm, False)

    # route 2: search by title, then verify
    print(f"\n      searching {len(without)} title(s) ...")
    for n, r in enumerate(without, 1):
        title = str(r.get("title") or "")
        ids = esearch_title(title)
        accepted = None
        for cand in ids:
            hit = efetch([cand]).get(cand)
            if not hit:
                continue
            if hit["title"] and title_matches(title, hit["title"]):
                accepted = (cand, hit, "exact" if norm_title(title) == norm_title(hit["title"])
                            else "prefix/substring")
                break
        if accepted:
            cand, hit, matched = accepted
            print(f"        [{n}/{len(without)}] idx={r['idx']:<7} -> pmid={cand} "
                  f"({matched}) abstract {len(hit['abstract'])} chars")
            record(hit, r, "esearch-by-title", cand, matched)
        else:
            print(f"        [{n}/{len(without)}] idx={r['idx']:<7} unresolved "
                  f"({len(ids)} candidate(s), none matched)  {title[:44]!r}")
            record(None, r, "esearch-by-title", "", False)

    out = P.resolve(base, a.out)
    P.write_csv(out, patch,
                ["idx", "citation_id", "method", "pmid", "title_match",
                 "abstract_chars", "doi", "authors", "journal", "year",
                 "pubmed_title", "abstract"])

    filled = [p for p in patch if p["abstract_chars"] > 0]
    with_doi = [p for p in patch if p["doi"]]
    print(f"\n[s13] patch written: {out}")
    print(f"      abstracts recovered : {len(filled)} / {len(patch)}")
    print(f"      DOIs recovered      : {len(with_doi)}")
    print(f"      still unresolved    : {len(patch) - len(filled)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
