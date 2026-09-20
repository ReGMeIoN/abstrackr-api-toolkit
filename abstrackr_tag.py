"""Apply our pass-1 codes as tags in abstrackr.

    POST /api/projects/{pid}/bulk-tags   {"citation_ids":[...], "tag":"...", "action":"add"}

Tag names are prefixed with "AI-p1:" so nobody mistakes them for a human
reviewer's codes, and each maps onto the protocol's exclusion reasons:

    candidate (F1/F2/F3/F5)   -> AI-p1:candidate-fulltext
    MR stream (M1)            -> AI-p1:MR-stream
    review (R1)               -> AI-p1:review-citation-chasing
    X1                        -> AI-p1:E1-no-exposure
    X2                        -> AI-p1:E1-outcome-not-CCA
    X3                        -> AI-p1:no-cancer-incidence-outcome
    X4                        -> AI-p1:E3-case-report-animal
    X5                        -> AI-p1:E2-E4-no-comparator
    exposure mismatch         -> AI-p1:exposure-mismatch

The exposure-mismatch case is a record our own precision pass flagged: it is
screened as "include" (conservative) but should be looked at first, because the
study's exposure is not cholecystectomy/gallstone disease.

Usage: python abstrackr_tag.py [--execute]
"""

import csv
import importlib.util
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("scr", os.path.join(HERE, "abstrackr_screen.py"))
scr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scr)

CODE_TAG = {
    "F1": "AI-p1:candidate-fulltext",
    "F2": "AI-p1:candidate-fulltext",
    "F3": "AI-p1:candidate-fulltext",
    "F5": "AI-p1:candidate-fulltext",
    "F6": "AI-p1:candidate-riskfactor-F6",
    "M1": "AI-p1:MR-stream",
    "R1": "AI-p1:review-citation-chasing",
    "X1": "AI-p1:E1-no-exposure",
    "X2": "AI-p1:E1-outcome-not-CCA",
    "X3": "AI-p1:no-cancer-incidence-outcome",
    "X4": "AI-p1:E3-case-report-animal",
    "X5": "AI-p1:E2-E4-no-comparator",
    "X8": "AI-p1:E6-duplicate-cohort",
    "X9": "AI-p1:X9-riskfactor-other-exposure",
}


def main():
    execute = "--execute" in sys.argv
    as_bot = "--as-bot" in sys.argv
    jar = scr.COOKIES_BOT if as_bot else scr.COOKIES
    mapping = list(csv.DictReader(open(scr.MAPPING, encoding="utf-8-sig")))
    groups = {}
    for m in mapping:
        tag = CODE_TAG.get(m["code"])
        if tag:
            groups.setdefault(tag, []).append(int(m["citation_id"]))

    # exposure-mismatch: our precision pass flagged these
    mism = os.path.join(scr.S, "exposure_mismatch.csv")
    if os.path.exists(mism):
        flagged = {r["pmid"].strip() for r in
                   csv.DictReader(open(mism, encoding="utf-8-sig")) if r["pmid"].strip()}
        ids = [int(m["citation_id"]) for m in mapping if m["pmid"].strip() in flagged]
        if ids:
            groups.setdefault("AI-p1:exposure-mismatch", []).extend(ids)
            print(f"exposure-mismatch records matched by pmid: {len(ids)}")

    total = sum(len(v) for v in groups.values())
    print(f"tag groups: {len(groups)}  total tag applications: {total}")
    for tag, ids in sorted(groups.items()):
        print(f"  {tag:<42} {len(ids)}")
    if not execute:
        print("\n(dry run) add --execute to apply")
        return 0

    s = scr.Session(jar)
    project = os.environ.get("ABSTRACKR_PROJECT", "")
    if not project:
        print("[!] 需要项目 id：设环境变量 ABSTRACKR_PROJECT=<id>")
        return 2
    for tag, ids in sorted(groups.items()):
        for i in range(0, len(ids), 200):
            chunk = ids[i:i + 200]
            st, body = s.call("POST", f"/api/projects/{project}/bulk-tags",
                              payload={"citation_ids": chunk, "tag": tag,
                                       "action": "add"})
            ok = st in (200, 201)
            print(f"  {tag:<42} +{len(chunk):>4} -> {st} "
                  f"{'' if ok else str(body)[:120]}")
            scr.audit(f"bulk-tags tag={tag} n={len(chunk)} status={st}")
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
