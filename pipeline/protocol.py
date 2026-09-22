"""Shared protocol loader and helpers for the screening pipeline.

Every stage of the pipeline reads **one JSON file** -- the *protocol* -- which pins
down everything that is review-specific:

    decision families and codes     F1 / X3 / R1 / M1 ...
    controlled vocabularies         form / exp / out / need
    conditional output schema       the fast lane, the `need` requirement
    workbook column names           idx / title / abstract / pmid / ...
    normalisation + audit rules     what may be auto-fixed, what is exported for humans
    platform mapping                label value per family, and the whole tag plan

The code stays generic; a new systematic review only needs a new `protocol.json`.
Start from `protocol.example.json` and fill in your own codes and vocabularies.

Why a loader at all: a typo in the protocol must fail **at the start of a stage**,
not halfway through a 9,000-record platform submission. `load_protocol()`
therefore validates the shape of every section a stage depends on, and every
stage resolves its paths and vocabularies through this module, so the stages can
never disagree about what a code means.
"""

import csv
import json
import os
import re
import sys

csv.field_size_limit(10_000_000)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROTOCOL = os.path.join(HERE, "protocol.example.json")

# Codes whose whole batch entry is just i/d/c/r (the "fast lane"). The pipeline
# reads this from the protocol; this constant only exists so error messages can
# be explicit when the section is missing.
_FALLBACK_GROUP_FIELDS = ["form", "exp", "out"]


class ProtocolError(Exception):
    """Raised when protocol.json is structurally unusable for a stage."""


# --------------------------------------------------------------------------- #
# paths / io
# --------------------------------------------------------------------------- #
def resolve(base, path):
    """Resolve `path` against `base` unless it is already absolute."""
    if not path:
        return path
    return path if os.path.isabs(path) else os.path.abspath(os.path.join(base, path))


def read_csv(path):
    """Read a CSV as a list of dicts; tolerant of the BOM Excel writes."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path, rows, columns=None):
    """Write rows (list of dict, or (header, rows) via columns) with a UTF-8 BOM."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    columns = list(columns or (rows[0].keys() if rows else []))
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def write_json(path, obj, indent=1):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=indent)
    return path


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def as_int(x, default=None):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# protocol loading + validation
# --------------------------------------------------------------------------- #
def load_protocol(path=None):
    """Load and validate a protocol file. Raises ProtocolError with a usable message."""
    path = path or DEFAULT_PROTOCOL
    if not os.path.exists(path):
        raise ProtocolError(
            f"protocol file not found: {path}\n"
            f"  copy pipeline/protocol.example.json into your workdir as "
            f"protocol.json, or pass --protocol <file>")
    try:
        proto = read_json(path)
    except Exception as exc:  # noqa: BLE001
        raise ProtocolError(f"{path} is not valid JSON: {exc}")
    if not isinstance(proto, dict):
        raise ProtocolError(f"{path}: top level must be a JSON object")
    proto["_path"] = os.path.abspath(path)
    _validate(proto)
    return proto


def _validate(proto):
    src = proto.get("_path", "protocol")

    def bad(msg):
        raise ProtocolError(f"{src}: {msg}")

    fams = proto.get("decision_families")
    if not isinstance(fams, dict) or not fams:
        bad("missing 'decision_families' (e.g. {\"F\": {\"hold\": true, \"codes\": [\"F1\"]}})")
    seen = {}
    for letter, spec in fams.items():
        if not isinstance(letter, str) or len(letter) != 1 or not letter.isalpha():
            bad(f"decision_families key {letter!r} must be a single letter "
                f"(it is the value of the `d` field)")
        if not isinstance(spec, dict):
            bad(f"decision_families[{letter}] must be an object")
        codes = spec.get("codes")
        if not isinstance(codes, list) or not codes:
            bad(f"decision_families[{letter}].codes must be a non-empty list")
        for c in codes:
            if not isinstance(c, str) or not c.upper().startswith(letter.upper()):
                bad(f"code {c!r} in family {letter} must start with that letter "
                    f"(the pipeline enforces code/family consistency)")
            if c.upper() in seen:
                bad(f"code {c!r} appears in two families ({seen[c.upper()]} and {letter})")
            seen[c.upper()] = letter

    vocab = proto.get("vocabularies")
    if not isinstance(vocab, dict):
        bad("missing 'vocabularies' (form / exp / out / need)")
    for name in group_fields(proto):
        if not isinstance(vocab.get(name), list) or not vocab[name]:
            bad(f"vocabularies.{name} must be a non-empty list "
                f"(it is the controlled vocabulary for the `{name}` field)")
    if hold_families(proto) and not vocab.get("need"):
        bad("vocabularies.need is required because some family has \"hold\": true")

    wb = proto.get("workbook")
    if not isinstance(wb, dict):
        bad("missing 'workbook' section")
    if not wb.get("id_column"):
        bad("workbook.id_column is required")

    plat = proto.get("platform")
    if plat is not None:
        vbf = plat.get("value_by_family")
        if not isinstance(vbf, dict) or not vbf:
            bad("platform.value_by_family must map each family letter to a label value")
        for letter in fams:
            if letter not in vbf:
                bad(f"platform.value_by_family is missing family {letter!r}")
        if not plat.get("tag_prefix"):
            bad("platform.tag_prefix is required when the platform section exists")


# --------------------------------------------------------------------------- #
# accessors
# --------------------------------------------------------------------------- #
def families(proto):
    return proto["decision_families"]


def family_letters(proto):
    return list(proto["decision_families"].keys())


def hold_families(proto):
    """Families whose records pass the current stage but are not yet decided
    (the Hold / Maybe pool)."""
    return [k for k, v in proto["decision_families"].items() if v.get("hold")]


def code_to_family(proto, code):
    """'x3' -> 'X'. Returns None for any code the protocol does not know."""
    code = str(code or "").strip().upper()
    if not code:
        return None
    for letter, spec in proto["decision_families"].items():
        if code in [str(c).upper() for c in spec["codes"]]:
            return letter
    return None


def all_codes(proto):
    out = []
    for letter, spec in proto["decision_families"].items():
        out.extend(str(c).upper() for c in spec["codes"])
    return out


def code_meaning(proto, code):
    return (proto.get("code_meanings") or {}).get(str(code or "").strip().upper(), "")


def vocab(proto, name):
    return [str(v).lower() for v in (proto.get("vocabularies") or {}).get(name, [])]


def schema(proto):
    return proto.get("schema") or {}


def group_fields(proto):
    return list(schema(proto).get("group_fields") or _FALLBACK_GROUP_FIELDS)


def fast_lane(proto):
    """{'code': 'X4', 'fields': ['i','d','c','r']} or None.

    The fast lane is a deliberate cost cut: one clearly-design-excluded code may
    skip form/exp/out/eff/conf. The trade-off is documented in docs/PIPELINE.md
    -- those records then carry no `form:` tag, so the PRISMA reason split cannot
    subdivide them.
    """
    fl = schema(proto).get("fast_lane")
    if not fl:
        return None
    code = str(fl.get("code", "")).upper()
    if not code:
        return None
    fields = [str(f) for f in (fl.get("fields") or ["i", "d", "c", "r"])]
    return {"code": code, "fields": fields}


def fast_lane_codes(proto):
    fl = fast_lane(proto)
    return {fl["code"]} if fl else set()


def decides_hold(proto, d):
    return str(d or "").upper()[:1] in hold_families(proto)


# --------------------------------------------------------------------------- #
# workbook
# --------------------------------------------------------------------------- #
def workbook_spec(proto):
    return proto.get("workbook") or {}


def workbook_path(proto, base):
    wb = workbook_spec(proto)
    return resolve(base, wb.get("records") or "records_enriched.csv")


def id_column(proto):
    return workbook_spec(proto).get("id_column") or "idx"


def load_workbook(proto, base):
    return read_csv(workbook_path(proto, base))


def batch_record(proto, row):
    """Project one workbook row onto the compact batch payload {i,y,j,t,a}."""
    mapping = workbook_spec(proto).get("batch_fields") or {
        "i": "idx", "y": "year", "j": "journal", "t": "title", "a": "abstract"}
    out = {}
    for short, column in mapping.items():
        out[short] = row.get(column, "") or ""
    out["i"] = as_int(out.get("i"), out.get("i"))
    return out


# --------------------------------------------------------------------------- #
# de-duplication (shared by the submit and verify stages)
# --------------------------------------------------------------------------- #
def norm_title(t):
    return re.sub(r"[^a-z0-9]+", "", (t or "").lower())[:120]


def cluster_roots(rows, id_column="idx", keys=("pmid", "doi", "title")):
    """Union-find over the de-duplication keys -> {member id: representative id}.

    The representative is the **smallest** id in the cluster. That is exactly the
    row the de-duplication stage kept, so one decision per unique record lines up
    with the platform fan-out. A raw union-find root is not necessarily the
    smallest member, which is the classic bug this function exists to avoid.

    `keys` is read from the protocol's `dedup.keys` by s7/s8, so a review that
    de-duplicates on different fields stays consistent between the local files
    and the platform write.
    """
    ids = [str(r[id_column]) for r in rows]
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    buckets = {}
    for r in rows:
        idx = str(r[id_column])
        for kind in keys:
            if kind == "title":
                val = norm_title(r.get("title"))
            else:
                val = str(r.get(kind) or "").strip().lower()
            if val:
                buckets.setdefault((kind, val), []).append(idx)
    for members in buckets.values():
        for other in members[1:]:
            union(members[0], other)

    comp = {}
    for idx in parent:
        comp.setdefault(find(idx), []).append(idx)
    rep = {}
    for members in comp.values():
        smallest = min(members, key=lambda s: (as_int(s) is None, as_int(s), s))
        for x in members:
            rep[x] = smallest
    return rep


def dedup_keys(proto):
    d = proto.get("dedup") or {}
    keys = d.get("keys") or ["pmid", "doi", "title"]
    return [str(k) for k in keys]


# --------------------------------------------------------------------------- #
# rule matching (normalisation + audit exports)
# --------------------------------------------------------------------------- #
def _as_list(v):
    return v if isinstance(v, list) else [v]


def rule_matches(when, dec, family):
    """Does a `when` block match a decision?

    Supported predicates (all optional, ANDed):
        code        code in list
        code_not    code NOT in list
        family      family letter in list
        form/exp/out  field value in list   (matched case-insensitively)
    """
    if not when:
        return False
    code = str(dec.get("c", "")).strip().upper()
    if "code" in when and code not in [str(x).upper() for x in _as_list(when["code"])]:
        return False
    if "code_not" in when and code in [str(x).upper() for x in _as_list(when["code_not"])]:
        return False
    if "family" in when:
        if (family or "").upper() not in [str(x).upper() for x in _as_list(when["family"])]:
            return False
    for field in ("form", "exp", "out"):
        if field in when:
            want = [str(x).lower() for x in _as_list(when[field])]
            if str(dec.get(field, "")).strip().lower() not in want:
                return False
    return True


def normalize_rules(proto):
    return proto.get("normalize_rules") or []


def audit_exports(proto):
    return proto.get("audit_exports") or []


# --------------------------------------------------------------------------- #
# platform mapping
# --------------------------------------------------------------------------- #
def platform_section(proto):
    return proto.get("platform") or {}


def label_value(proto, family):
    v = platform_section(proto).get("value_by_family", {})
    return v.get(family)


def label_name(proto, value):
    names = platform_section(proto).get("label_names") or {}
    return names.get(str(value), str(value))


def _meta_matches(when, meta_row):
    """Evaluate a `when_meta` block against a workbook row.

    Shape: {"column": "has_abstract", "not_in": ["yes"]}  (or `in` / `equals` /
    `not_equals`). Written explicitly rather than guessed from the column name,
    so a protocol can attach `qs:no-abstract` to whatever column the review
    actually uses.
    """
    if not when:
        return False
    col = when.get("column")
    if not col:
        return False
    actual = str(meta_row.get(col, "")).strip().lower()

    def norm(v):
        return str(v).strip().lower()

    if "in" in when and actual not in [norm(v) for v in _as_list(when["in"])]:
        return False
    if "not_in" in when and actual in [norm(v) for v in _as_list(when["not_in"])]:
        return False
    if "equals" in when and actual != norm(when["equals"]):
        return False
    if "not_equals" in when and actual == norm(when["not_equals"]):
        return False
    return True


def build_tags(proto, dec, root, meta_row, is_dup_copy):
    """The full tag set a record's decision earns on the platform.

    One function, used by both the writer (s7) and the verifier (s8), so the two
    can never drift apart -- which is the whole reason verification is meaningful.
    """
    plat = platform_section(proto)
    family = str(dec.get("d", "")).upper()[:1]
    code = str(dec.get("c", "")).strip()
    fast = fast_lane(proto)
    fast_here = bool(fast and code.upper() == fast["code"])

    tags = [f"{plat.get('tag_prefix', 'AI')}:{code}"]
    for field in group_fields(proto):
        if fast_here and field not in fast["fields"]:
            continue
        v = str(dec.get(field, "")).strip().lower()
        if v:
            tags.append(f"{field}:{v}")
    for rule in plat.get("role_tags") or []:
        hit = False
        if rule.get("hold") is True and family in hold_families(proto):
            hit = True
        if rule.get("codes") and code.upper() in [str(c).upper() for c in rule["codes"]]:
            hit = True
        if hit:
            tags.append(rule["tag"])

    if meta_row is not None:
        for rule in plat.get("meta_tags") or []:
            if _meta_matches(rule.get("when_meta"), meta_row):
                tags.append(rule["tag"])

    dup = plat.get("dup_tags") or {}
    if dup:
        key = "copy" if is_dup_copy else "keep"
        if dup.get(key):
            tags.append(dup[key])
    return tags


# --------------------------------------------------------------------------- #
# cli helpers
# --------------------------------------------------------------------------- #
def common_args(parser):
    parser.add_argument("--protocol", default=None,
                        help="protocol JSON (default: <dir>/protocol.json, else "
                             "pipeline/protocol.example.json)")
    parser.add_argument("--dir", required=True,
                        help="screening workdir (holds the workbook, batches/, decisions/)")
    return parser


def resolve_protocol(args):
    """--protocol, else <dir>/protocol.json, else the shipped example."""
    if args.protocol:
        return load_protocol(resolve(os.getcwd(), args.protocol))
    candidate = os.path.join(os.path.abspath(args.dir), "protocol.json")
    if os.path.exists(candidate):
        return load_protocol(candidate)
    return load_protocol(DEFAULT_PROTOCOL)


def outdir(args, default_subdir=""):
    d = getattr(args, "outdir", None)
    base = os.path.abspath(d) if d else os.path.abspath(args.dir)
    return os.path.join(base, default_subdir) if default_subdir else base


def banner(stage, msg):
    print(f"[{stage}] {msg}")
    sys.stdout.flush()
