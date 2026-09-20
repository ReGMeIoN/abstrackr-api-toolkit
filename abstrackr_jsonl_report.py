"""Parse an abstrackr full-project JSONL export into review-ready outputs.

abstrackr (https://abstrackr.com) does not publish a REST API for review data.
Its official programmatic interface is the "Full Project Export": a Leader
downloads project.zip from the Citation Manager, which contains a JSONL file
with one citation per line:

    { "id": 42, "title": "...", "status": "included", "probability": 0.91,
      "labels": [ {"user_id": 3, "email": "...", "value": 1, "created_at": "..."} ],
      "resolution_labels": [], "tags": [ {"name": "RCT", "email": "..."} ],
      "notes": [] }

Label values: 1 = Include, -1 = Exclude, 0 = Maybe.

This script turns that file into everything the review needs: PRISMA counts,
per-reviewer activity, inter-rater agreement (Cohen's kappa), the conflict list,
and the included / excluded citation lists.

Usage:
    python abstrackr_jsonl_report.py <project.jsonl> [--outdir DIR]
    python abstrackr_jsonl_report.py <project.zip> [--outdir DIR]

Outputs (into --outdir, default ./abstrackr):
    prisma_numbers.md      PRISMA-style counts
    prisma_numbers.csv     same, machine readable
    reviewer_activity.csv  labels per reviewer
    agreement.csv          pairwise agreement and Cohen's kappa
    conflicts.csv          citations still in conflict
    included.csv           included citations
    excluded.csv           excluded citations
    unscreened.csv         not yet screened
"""

import argparse
import csv
import io
import json
import os
import sys
import zipfile
from collections import Counter, defaultdict
from itertools import combinations

LABEL_NAME = {1: "include", -1: "exclude", 0: "maybe", None: "no_label"}


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------
def load_records(path):
    """Read records from a .jsonl file or a project .zip containing one."""
    lines = []

    if path.lower().endswith(".zip"):
        with zipfile.ZipFile(path) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".jsonl")]
            if not names:
                raise SystemExit(f"no .jsonl found inside {path}")
            for n in names:
                with z.open(n) as fh:
                    lines.extend(io.TextIOWrapper(fh, encoding="utf-8").read().splitlines())
    else:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()

    recs = []
    bad = 0
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            recs.append(json.loads(ln))
        except json.JSONDecodeError:
            bad += 1
    if bad:
        print(f"WARNING: {bad} line(s) could not be parsed as JSON and were skipped")
    return recs


def label_of(rec):
    """Resolve a record's final decision: resolution label wins over raw labels."""
    for rl in rec.get("resolution_labels") or []:
        v = rl.get("value") if isinstance(rl, dict) else rl
        if v is not None:
            return v, "resolution"
    vals = [l.get("value") for l in (rec.get("labels") or []) if isinstance(l, dict)]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, "none"
    if len(set(vals)) == 1:
        return vals[0], "labels_agree"
    return None, "labels_disagree"


# ---------------------------------------------------------------------------
# kappa
# ---------------------------------------------------------------------------
def cohen_kappa(pairs):
    """Cohen's kappa for two raters over a list of (a, b) label pairs."""
    if not pairs:
        return None, None, None, 0
    n = len(pairs)
    cats = sorted({x for p in pairs for x in p})
    obs = sum(1 for a, b in pairs if a == b) / n
    pa = Counter(a for a, _ in pairs)
    pb = Counter(b for _, b in pairs)
    exp = sum((pa[c] / n) * (pb[c] / n) for c in cats)
    kappa = (obs - exp) / (1 - exp) if exp != 1 else float("nan")
    return obs, exp, kappa, n


def interpret_kappa(k):
    if k is None:
        return ""
    if k != k:  # NaN
        return "undefined (no expected disagreement)"
    if k < 0:
        return "poor"
    if k < 0.21:
        return "slight"
    if k < 0.41:
        return "fair"
    if k < 0.61:
        return "moderate"
    if k < 0.81:
        return "substantial"
    return "almost perfect"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("jsonl")
    ap.add_argument("--outdir", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "abstrackr"))
    args = ap.parse_args()

    if not os.path.exists(args.jsonl):
        raise SystemExit(f"file not found: {args.jsonl}")

    os.makedirs(args.outdir, exist_ok=True)
    recs = load_records(args.jsonl)
    print(f"records loaded: {len(recs)}")

    # ---- status overview -----------------------------------------------------
    status_counts = Counter((r.get("status") or "unknown") for r in recs)

    # ---- per reviewer --------------------------------------------------------
    per_reviewer = defaultdict(Counter)
    reviewer_citations = defaultdict(set)
    reviewer_label = {}          # (citation_id, email) -> value
    for r in recs:
        cid = r.get("id")
        for l in (r.get("labels") or []):
            if not isinstance(l, dict):
                continue
            email = (l.get("email") or f"user_{l.get('user_id')}").strip()
            v = l.get("value")
            if v not in (1, -1, 0):
                continue
            per_reviewer[email][LABEL_NAME[v]] += 1
            reviewer_citations[email].add(cid)
            reviewer_label[(cid, email)] = v

    reviewers = sorted(per_reviewer)
    print(f"reviewers found: {len(reviewers)}  -> {', '.join(reviewers)}")

    # ---- pairwise agreement --------------------------------------------------
    agreement_rows = []
    for a, b in combinations(reviewers, 2):
        common = reviewer_citations[a] & reviewer_citations[b]
        pairs = [(reviewer_label[(c, a)], reviewer_label[(c, b)]) for c in common]
        obs, exp, k, n = cohen_kappa(pairs)
        agreement_rows.append({
            "reviewer_a": a, "reviewer_b": b,
            "citations_both_screened": n,
            "observed_agreement": f"{obs:.4f}" if obs is not None else "",
            "expected_agreement": f"{exp:.4f}" if exp is not None else "",
            "cohen_kappa": f"{k:.4f}" if (k is not None and k == k) else "",
            "interpretation": interpret_kappa(k),
        })

    # ---- write outputs -------------------------------------------------------
    def write_csv(name, rows, fields=None):
        p = os.path.join(args.outdir, name)
        with open(p, "w", encoding="utf-8-sig", newline="") as fh:
            if not rows:
                fh.write("")
                return p
            w = csv.DictWriter(fh, fieldnames=fields or list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        return p

    # citations by final decision
    buckets = {"included": [], "excluded": [], "maybe": [], "unscreened": [], "conflict": []}
    for r in recs:
        v, src = label_of(r)
        st = (r.get("status") or "").lower()
        row = {
            "id": r.get("id"),
            "title": r.get("title", ""),
            "status": r.get("status", ""),
            "probability": r.get("probability", ""),
            "final_label": LABEL_NAME.get(v, ""),
            "label_source": src,
            "n_labels": len(r.get("labels") or []),
            "n_tags": len(r.get("tags") or []),
            "n_notes": len(r.get("notes") or []),
        }
        if st == "conflict" or src == "labels_disagree":
            buckets["conflict"].append(row)
        if v == 1:
            buckets["included"].append(row)
        elif v == -1:
            buckets["excluded"].append(row)
        elif v == 0:
            buckets["maybe"].append(row)
        else:
            buckets["unscreened"].append(row)

    for name in ("included", "excluded", "maybe", "unscreened", "conflict"):
        write_csv(f"{name}.csv", buckets[name])

    act_rows = []
    for email in reviewers:
        c = per_reviewer[email]
        total = sum(c.values())
        act_rows.append({
            "reviewer": email,
            "total_labels": total,
            "include": c.get("include", 0),
            "exclude": c.get("exclude", 0),
            "maybe": c.get("maybe", 0),
            "exclude_rate": f"{(c.get('exclude', 0) / total):.3f}" if total else "",
        })
    write_csv("reviewer_activity.csv", act_rows)
    write_csv("agreement.csv", agreement_rows)

    p_rows = [{"status": k, "n": v} for k, v in sorted(status_counts.items(), key=lambda x: -x[1])]
    write_csv("prisma_numbers.csv", p_rows)

    lines = []
    lines.append("# PRISMA numbers from abstrackr export\n")
    lines.append(f"Source file: `{os.path.abspath(args.jsonl)}`\n")
    lines.append(f"Records in export: **{len(recs)}**\n")
    lines.append("\n## Status as reported by abstrackr\n")
    lines.append("| status | n |")
    lines.append("|---|---|")
    for k, v in sorted(status_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {k} | {v} |")
    lines.append("\n## Final decision (resolution label overrides raw labels)\n")
    lines.append("| decision | n |")
    lines.append("|---|---|")
    for key in ("included", "excluded", "maybe", "unscreened"):
        lines.append(f"| {key} | {len(buckets[key])} |")
    lines.append(f"| still in conflict | {len(buckets['conflict'])} |")
    lines.append("\n## Reviewer activity\n")
    lines.append("| reviewer | labels | include | exclude | maybe | exclude rate |")
    lines.append("|---|---|---|---|---|---|")
    for r in act_rows:
        lines.append(f"| {r['reviewer']} | {r['total_labels']} | {r['include']} | "
                     f"{r['exclude']} | {r['maybe']} | {r['exclude_rate']} |")
    if agreement_rows:
        lines.append("\n## Inter-rater agreement (Cohen's kappa)\n")
        lines.append("| reviewer A | reviewer B | both screened | observed | expected | kappa | interpretation |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in agreement_rows:
            lines.append(f"| {r['reviewer_a']} | {r['reviewer_b']} | {r['citations_both_screened']} | "
                         f"{r['observed_agreement']} | {r['expected_agreement']} | "
                         f"{r['cohen_kappa']} | {r['interpretation']} |")
        lines.append("\nPer the protocol, title/abstract screening targets kappa >= 0.70 "
                     "and full-text screening kappa >= 0.80.")
    else:
        lines.append("\n## Inter-rater agreement\n\nOnly one reviewer contributed labels, "
                     "so agreement could not be computed.\n")
    lines.append("\n## Files written\n")
    for n in ("prisma_numbers.md", "prisma_numbers.csv", "reviewer_activity.csv",
              "agreement.csv", "conflicts.csv", "included.csv", "excluded.csv",
              "unscreened.csv"):
        lines.append(f"- `{n}`")

    with open(os.path.join(args.outdir, "prisma_numbers.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print()
    print("\n".join(lines))
    print()
    print(f"outputs written to: {os.path.abspath(args.outdir)}")


if __name__ == "__main__":
    main()
