"""Stage 5 -- build the Hold pool: the worklist the full-text stage consumes.

The title/abstract stage never decides "Include". A record that survives it is a
**Hold**: no exclusion criterion applies, there is a positive PECO signal, but
something is missing -- and the `need` field says exactly what is missing. This
stage turns all of that into the worklist.

Outputs
    hold_fulltext.json   records carrying hold_reason + need_fulltext (feeds the LLM)
    hold_fulltext.csv    the same as a table, sorted so a human hunting PDFs works
                         code by code and, inside a code, by what is missing
    hold_summary.md      counts by code / by need / by form

The `need` distribution is the useful part: it says how many PDFs must be opened
just to learn the study design, versus how many only need an effect size.

usage
    python s5_hold_pool.py --dir <workdir> [--protocol p.json]
"""

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402


def decisions_source(base):
    """Prefer the normalised map; fall back to the raw fused map."""
    for name in ("all_decisions_normalized.json", "all_decisions.json"):
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    src = decisions_source(base)
    if not src:
        P.banner("s5", "no all_decisions[_normalized].json -- run s3/s4 first")
        return 2
    P.banner("s5", f"decisions source: {os.path.basename(src)}")

    dec = {int(k): v for k, v in P.read_json(src).items()}
    book = P.load_workbook(proto, base)
    idcol = P.id_column(proto)
    meta = P.workbook_spec(proto).get("meta") or {}
    holds = P.hold_families(proto)
    sch = P.schema(proto)
    P.banner("s5", f"workbook rows: {len(book)}   decisions: {len(dec)}   "
                   f"hold families: {holds}")

    out = []
    for r in book:
        i = P.as_int(r.get(idcol))
        d = dec.get(i)
        if not d:
            continue
        if str(d.get("d", "")).upper()[:1] not in holds:
            continue
        need = d.get("need")
        if isinstance(need, str):
            need = [t.strip() for t in need.split(",") if t.strip()]
        item = {
            "i": i,
            "c": d.get("c", ""),
            "decision": "Hold",
            "title": r.get("title", ""),
            "abstract": r.get("abstract", ""),
            "hold_reason": d.get(sch.get("reason_field", "r"), ""),
            "hold_evidence": d.get(sch.get("evidence_field", "s"), ""),
            "need_fulltext": need if isinstance(need, list) else [],
            "form": d.get("form", ""),
            "exp": d.get("exp", ""),
            "out": d.get("out", ""),
            "eff": d.get(sch.get("effect_field", "eff"), ""),
            "conf": d.get(sch.get("confidence_field", "conf"), ""),
        }
        for key, column in meta.items():
            item[key] = r.get(column, "")
        out.append(item)

    order = {c: n for n, c in enumerate((proto.get("hold_pool") or {}).get("sort_codes") or [])}
    out.sort(key=lambda x: (order.get(x["c"], 99), x["need_fulltext"], x["i"]))

    hp = proto.get("hold_pool") or {}
    jp = P.write_json(os.path.join(base, hp.get("file_json", "hold_fulltext.json")), out)
    P.banner("s5", f"written: {jp}  ({len(out)} records)")

    cols = ["i"] + [k for k in meta] + ["c", "form", "exp", "out", "need_fulltext",
                                        "conf", "eff", "hold_reason", "hold_evidence",
                                        "title", "abstract"]
    rows = []
    for r in out:
        row = dict(r)
        row["need_fulltext"] = "|".join(r["need_fulltext"])
        rows.append(row)
    cp = P.write_csv(os.path.join(base, hp.get("file_csv", "hold_fulltext.csv")), rows, cols)
    P.banner("s5", f"written: {cp}")

    by_code = Counter(r["c"] for r in out)
    by_need = Counter(n for r in out for n in r["need_fulltext"])
    by_form = Counter(r["form"] for r in out)
    meta_abs = meta.get("has_abstract", "has_abstract")
    no_abs = sum(1 for r in out if str(r.get(meta_abs, "")).strip().lower() != "yes")

    L = ["# Hold pool (passed title/abstract, awaiting full text)", "",
         f"- **Hold total: {len(out)}**",
         f"- of which judged from the title alone (no abstract): {no_abs}", "",
         "## By title/abstract code", "", "| code | meaning | n |", "|---|---|---|"]
    for c, n in by_code.most_common():
        L.append(f"| {c} | {P.code_meaning(proto, c)} | {n} |")
    L += ["", "## What the full text must settle (`need`)", "", "| gap | n |", "|---|---|"]
    for k, n in by_need.most_common():
        L.append(f"| {k} | {n} |")
    L += ["", "## By document form", "", "| form | n |", "|---|---|---|"]
    for k, n in by_form.most_common():
        L.append(f"| {k} | {n} |")
    tag_hold = next((r["tag"] for r in (P.platform_section(proto).get("role_tags") or [])
                     if r.get("hold")), "hold:fulltext")
    L += ["", f"> Platform side: these records are labelled "
              f"**{P.label_name(proto, P.label_value(proto, holds[0]))}** and tagged "
              f"`{P.platform_section(proto).get('tag_prefix', 'AI')}:<code>` + `{tag_hold}`.",
          "> The full-text stage must return a verdict for every one of them "
          "(closure check: no Hold record may be left undecided)."]
    sm = os.path.join(base, hp.get("summary_md", "hold_summary.md"))
    with open(sm, "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    P.banner("s5", f"written: {sm}")

    print(f"\n  Hold total: {len(out)}")
    print(f"    by code: {dict(by_code)}")
    print(f"    by need: {dict(by_need)}")
    print(f"    by form: {dict(by_form)}")
    print(f"    no abstract: {no_abs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
