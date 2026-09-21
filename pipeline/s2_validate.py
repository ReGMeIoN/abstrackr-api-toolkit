"""Stage 2 -- validate every batch decision file against its batch input.

This is the **central validator** and the single most important control in the
pipeline. Screening subagents must never write their own validator (see
docs/PIPELINE.md, "Orchestration discipline"): one validator, run once per pass,
with the same verdict for everyone.

Checks, per batch:
  * the file parses and carries a `decisions` array
  * no duplicate ids, no invented ids, and the id set equals the batch's exactly
  * `d` is a known family letter and `c` belongs to that family
    (this is what stops an `X` record from carrying a `c=R1`)
  * the reason field is non-empty
  * the conditional schema holds: the fast-lane code carries ONLY i/d/c/r, and
    every other record carries the group fields (form/exp/out)
  * every value is inside the protocol's controlled vocabulary
  * Hold records (families flagged `"hold": true`) declare a non-empty `need`
    drawn from the `need` vocabulary
  * `conf`, when present, is one of the allowed values

Exit code is 0 only when nothing failed, so the stage can gate a pipeline run.

usage
    python s2_validate.py --dir <workdir> [--protocol p.json]
        [--batch b_001] [--prefix b_] [--fuse]
"""

import argparse
import os
import re
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import protocol as P  # noqa: E402


def check(proto, base, name, batch_dir, dec_dir):
    bp = os.path.join(base, batch_dir, name + ".json")
    dp = os.path.join(base, dec_dir, name + ".decisions.json")
    if not os.path.exists(dp):
        return "MISSING", f"no decisions file ({dp})", {}
    try:
        batch = P.read_json(bp)
        dec = P.read_json(dp)
    except Exception as exc:  # noqa: BLE001
        return "BAD", f"unreadable: {exc}", {}

    fams = P.families(proto)
    fam_codes = {k: [str(c).upper() for c in v["codes"]] for k, v in fams.items()}
    holds = P.hold_families(proto)
    sch = P.schema(proto)
    rfield = sch.get("reason_field", "r")
    cfield = sch.get("confidence_field", "conf")
    max_r = sch.get("max_reason_chars")
    fast = P.fast_lane(proto)
    vocab = {f: P.vocab(proto, f) for f in P.group_fields(proto)}
    need_vocab = P.vocab(proto, "need")

    want = {P.as_int(r.get("i")) for r in batch.get("records", [])}
    entries = dec.get("decisions", [])
    problems = []
    got = [P.as_int(e.get("i")) for e in entries]
    if len(got) != len(set(got)):
        problems.append(f"duplicate i ({len(got)} entries, {len(set(got))} unique)")

    ints = []
    for e in entries:
        i = P.as_int(e.get("i"))
        if i is None:
            problems.append(f"non-integer i: {e.get('i')!r}")
            continue
        ints.append(i)

        d = str(e.get("d", "")).upper()[:1]
        c = str(e.get("c", "")).strip().upper()
        if d not in fams:
            problems.append(f"i={i} unknown family d={e.get('d')!r}")
        elif c not in fam_codes[d]:
            problems.append(f"i={i} code {c!r} does not belong to family {d}")

        reason = str(e.get(rfield, "")).strip()
        if not reason:
            problems.append(f"i={i} empty {rfield}")
        elif max_r and len(reason) > max_r:
            problems.append(f"i={i} {rfield} is {len(reason)} chars (max {max_r})")

        # --- conditional schema -------------------------------------------------
        if fast and c == fast["code"]:
            extra = [k for k in P.group_fields(proto) + ["need", sch.get("effect_field", "eff")]
                     if str(e.get(k, "")).strip() not in ("", "[]")]
            if extra:
                problems.append(f"i={i} fast lane ({fast['code']}) must not carry {extra}")
            continue

        for field in P.group_fields(proto):
            v = str(e.get(field, "")).strip().lower()
            if not v:
                problems.append(f"i={i} missing {field}")
            elif v not in vocab[field]:
                problems.append(f"i={i} bad {field}={e.get(field)!r}")
        if d in holds:
            need = e.get("need")
            if isinstance(need, str):
                need = [t.strip() for t in need.split(",") if t.strip()]
            if not isinstance(need, list) or not need:
                problems.append(f"i={i} Hold record ({d}) without need[]")
            else:
                bad = [x for x in need if str(x).strip().lower() not in need_vocab]
                if bad:
                    problems.append(f"i={i} bad need={bad}")

        conf = str(e.get(cfield, "")).strip().lower()
        if conf and conf not in P.schema(proto).get("confidence_values", []):
            problems.append(f"i={i} bad {cfield}={e.get(cfield)!r}")

    s = set(ints)
    if s != want:
        problems.append(f"i-set mismatch: missing {sorted(x for x in want - s if x is not None)[:8]} "
                        f"extra {sorted(x for x in s - want if x is not None)[:8]}")
    if dec.get("n") not in (None, len(batch.get("records", []))):
        problems.append(f"declared n={dec.get('n')} != batch n={len(batch.get('records', []))}")
    return ("OK" if not problems else "FAIL"), "; ".join(problems[:6]), {"n": len(ints)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    P.common_args(ap)
    ap.add_argument("--batch", help="validate a single batch by name")
    ap.add_argument("--prefix", default="b_", help="batch filename prefix to enumerate")
    ap.add_argument("--batch-dir", default="batches")
    ap.add_argument("--dec-dir", default="decisions")
    ap.add_argument("--fuse", action="store_true",
                    help="also write all_decisions.json from the valid batches")
    a = ap.parse_args()

    proto = P.resolve_protocol(a)
    base = os.path.abspath(a.dir)
    bdir = os.path.join(base, a.batch_dir)

    if a.batch:
        names = [a.batch]
    elif os.path.isdir(bdir):
        pref = re.escape(a.prefix)
        names = sorted(f[:-5] for f in os.listdir(bdir)
                       if re.fullmatch(pref + r"\d+\.json", f))
    else:
        P.banner("s2", f"no batch directory at {bdir}")
        return 2
    if not names:
        P.banner("s2", f"no batches matching {a.prefix}* in {bdir}")
        return 2

    ok = bad = miss = 0
    fused = {}
    for name in names:
        status, msg, _ = check(proto, base, name, a.batch_dir, a.dec_dir)
        ok += status == "OK"
        bad += status == "FAIL"
        miss += status == "MISSING"
        if status != "OK":
            print(f"  {name:<8} {status:<8} {msg}")
        if status == "OK":
            d = P.read_json(os.path.join(base, a.dec_dir, name + ".decisions.json"))
            for x in d["decisions"]:
                fused[P.as_int(x["i"])] = x

    P.banner("s2", f"total {len(names)}: OK {ok} / FAIL {bad} / MISSING {miss}")
    if a.fuse and fused:
        out = os.path.join(base, "all_decisions.json")
        P.write_json(out, {str(k): v for k, v in sorted(fused.items())})
        dist = Counter(str(v.get("d", "")).upper()[:1] for v in fused.values())
        P.banner("s2", f"fused {len(fused)} decisions -> {out}  families={dict(dist)}")
    return 0 if bad == 0 and miss == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
