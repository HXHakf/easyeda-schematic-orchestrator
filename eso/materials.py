"""Private immutable, content-addressed host-reviewed facts; never a PDF certifier."""

import ast
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from copy import deepcopy
from jsonschema import Draft202012Validator
from .core import Refused, digest, dump, file_digest, load, now
from .knowledge_input import obj, STRING

SHA = {"type": "string", "pattern": "^[a-f0-9]{64}$"}
TEXT = {"type": "string", "minLength": 1, "pattern": r"\S"}
NUMBER = {"type": "number", "minimum": -1e12, "maximum": 1e12}
PAIR = {"type": "array", "items": NUMBER, "minItems": 2, "maxItems": 2}
COVERAGE = obj(
    {
        **{
            k: PAIR
            for k in (
                "ambient_c",
                "supply_v",
                "control_v",
                "load_current_a",
                "frequency_hz",
            )
        },
        "thermal_context": TEXT,
    },
    (
        "ambient_c",
        "supply_v",
        "control_v",
        "load_current_a",
        "frequency_hz",
        "thermal_context",
    ),
)
FACT = obj(
    {
        "value": NUMBER,
        "unit": TEXT,
        "basis": {"enum": ["guaranteed", "typical", "derived"]},
        "bound": {"enum": ["lower", "upper", "nominal"]},
        "section": TEXT,
        "test_conditions": TEXT,
        "applicability": obj(
            {"state": {"enum": ["all", "on", "off"]}, "coverage": COVERAGE},
            ("state", "coverage"),
        ),
        "derivation": obj(
            {
                "formula": TEXT,
                "inputs": {
                    "type": "array",
                    "items": TEXT,
                    "minItems": 1,
                    "uniqueItems": True,
                },
            },
            ("formula", "inputs"),
        ),
    },
    ("value", "unit", "basis", "bound", "section", "test_conditions"),
)
DOC = obj(
    {
        **{
            k: TEXT
            for k in ("model", "package", "revision", "pinout_section", "reviewed_by")
        },
        "sha256": SHA,
    },
    ("model", "package", "revision", "pinout_section", "reviewed_by", "sha256"),
)
RECORD = obj(
    {
        "schema_version": {"const": 2},
        **{k: TEXT for k in ("manufacturer", "model", "package")},
        "kind": {"enum": ["npn", "nmos", "resistor", "mcu"]},
        "datasheet": DOC,
        "pins": {"type": "object", "minProperties": 2, "additionalProperties": TEXT},
        "facts": {
            "type": "object",
            "minProperties": 1,
            "propertyNames": {"pattern": "^[a-z][a-z0-9_]*$"},
            "additionalProperties": FACT,
        },
        "coverage": COVERAGE,
        "coverage_review": TEXT,
    },
    (
        "schema_version",
        "manufacturer",
        "model",
        "package",
        "kind",
        "datasheet",
        "pins",
        "facts",
        "coverage",
        "coverage_review",
    ),
)


# Old records omit status and retain the full reviewed-record requirements.
RECORD["properties"]["status"] = {"const": "reviewed"}
DRAFT_RECORD = deepcopy(RECORD)
DRAFT_RECORD["properties"]["status"] = {"const": "draft"}
DRAFT_RECORD["properties"]["missing_evidence"] = {
    "type": "array",
    "items": TEXT,
    "minItems": 1,
    "uniqueItems": True,
}
DRAFT_RECORD["required"] = [
    key for key in RECORD["required"] if key not in ("coverage", "coverage_review")
] + ["status", "missing_evidence"]
for key in ("coverage", "coverage_review"):
    DRAFT_RECORD["properties"].pop(key)
DRAFT_RECORD["properties"]["facts"]["additionalProperties"]["properties"]["basis"] = {
    "enum": ["guaranteed", "typical"]
}
DRAFT_RECORD["properties"]["facts"]["additionalProperties"]["properties"].pop(
    "derivation"
)
RECORD = {"oneOf": [RECORD, DRAFT_RECORD]}


def shape(value, schema):
    errors = list(Draft202012Validator(schema).iter_errors(value))
    if errors:
        raise Refused(
            "; ".join(".".join(map(str, e.path)) + ": " + e.message for e in errors)
        )

    def finite(v):
        if isinstance(v, float) and not math.isfinite(v):
            raise Refused("nonfinite material value")
        if isinstance(v, dict):
            for x in v.values():
                finite(x)
        if isinstance(v, list):
            for x in v:
                finite(x)

    finite(value)


def fact_unit(key):
    for suffix, unit in (
        ("_ohm", "ohm"),
        ("_ma", "mA"),
        ("_ua", "uA"),
        ("_a", "A"),
        ("_v", "V"),
        ("_w", "W"),
        ("_c", "degC"),
    ):
        if key.endswith(suffix):
            return unit
    return "1"


def validate_record(record):
    shape(record, RECORD)
    for key in ("model", "package"):
        if record[key] != record["datasheet"][key]:
            raise Refused("datasheet identity mismatch: " + key)
    pins = record["pins"]
    expected = {
        "npn": {"B", "C", "E"},
        "nmos": {"G", "D", "S"},
        "resistor": {"1", "2"},
        "mcu": set(pins),
    }[record["kind"]]
    if set(pins) != expected or len(set(pins.values())) != len(pins):
        raise Refused("invalid physical pin mapping")
    if record["kind"] == "mcu" and set(pins.values()) != {str(i) for i in range(1, 49)}:
        raise Refused("bounded MCU requires full LQFP48 pin inventory")
    if record.get("status") == "draft":
        for key, fact in record["facts"].items():
            if fact["unit"] != fact_unit(key):
                raise Refused("unsupported/wrong canonical unit: " + key)
            for axis, limits in (
                fact.get("applicability", {}).get("coverage", {}).items()
            ):
                if axis != "thermal_context" and limits[0] > limits[1]:
                    raise Refused("reversed source applicability: " + key + "." + axis)
        return record
    for key, bounds in record["coverage"].items():
        if key != "thermal_context" and bounds[0] > bounds[1]:
            raise Refused("reversed coverage: " + key)
    facts = record["facts"]
    visiting = set()
    visited = set()

    def check(key):
        if key in visiting:
            raise Refused("cyclic fact derivation")
        if key not in facts:
            raise Refused("missing derivation input: " + key)
        if key in visited:
            return
        visiting.add(key)
        f = facts[key]
        if f["unit"] != fact_unit(key):
            raise Refused("unsupported/wrong canonical unit: " + key)
        if f["basis"] == "derived":
            d = f.get("derivation")
            if not d:
                raise Refused("derived fact needs formula and inputs")
            for dep in d["inputs"]:
                check(dep)
                if facts[dep]["basis"] == "typical":
                    raise Refused("typical input cannot derive a guarantee")
            try:
                tree = ast.parse(d["formula"], mode="eval")
            except (SyntaxError, RecursionError) as error:
                raise Refused("invalid derivation") from error
            names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
            if names != set(d["inputs"]):
                raise Refused("derivation inputs differ from formula")

            def calculate(node):
                if isinstance(node, ast.Expression):
                    return calculate(node.body)
                if isinstance(node, ast.Name):
                    return facts[node.id]["value"]
                if isinstance(node, ast.Constant) and type(node.value) in (int, float):
                    return node.value
                if isinstance(node, ast.UnaryOp) and isinstance(
                    node.op, (ast.UAdd, ast.USub)
                ):
                    return calculate(node.operand) * (
                        1 if isinstance(node.op, ast.UAdd) else -1
                    )
                if isinstance(node, ast.BinOp):
                    a, b = calculate(node.left), calculate(node.right)
                    if isinstance(node.op, ast.Add):
                        return a + b
                    if isinstance(node.op, ast.Sub):
                        return a - b
                    if isinstance(node.op, ast.Mult):
                        return a * b
                    if isinstance(node.op, ast.Div):
                        return a / b
                raise Refused(
                    "only named facts and arithmetic allowed; no evaluation of code"
                )

            def interval(node):
                if isinstance(node, ast.Expression):
                    return interval(node.body)
                if isinstance(node, ast.Name):
                    source = facts[node.id]
                    if source["bound"] == "upper":
                        return -math.inf, source["value"]
                    if source["bound"] == "lower":
                        return source["value"], math.inf
                    return -math.inf, math.inf
                if isinstance(node, ast.Constant):
                    return node.value, node.value
                if isinstance(node, ast.UnaryOp):
                    lower, upper = interval(node.operand)
                    return (
                        (-upper, -lower)
                        if isinstance(node.op, ast.USub)
                        else (lower, upper)
                    )
                left, right = interval(node.left), interval(node.right)
                if isinstance(node.op, ast.Add):
                    return left[0] + right[0], left[1] + right[1]
                if isinstance(node.op, ast.Sub):
                    return left[0] - right[1], left[1] - right[0]
                if isinstance(node.op, ast.Div):
                    if right[0] <= 0 <= right[1]:
                        raise Refused("derivation divisor interval includes zero")
                    right = (1 / right[1], 1 / right[0])
                products = [a * b for a in left for b in right]
                if any(math.isnan(v) for v in products):
                    return -math.inf, math.inf
                return min(products), max(products)

            try:
                value = calculate(tree)
            except (ArithmeticError, RecursionError) as error:
                raise Refused("invalid derivation arithmetic") from error
            if not math.isfinite(value) or not math.isclose(
                value, f["value"], rel_tol=1e-9, abs_tol=1e-15
            ):
                raise Refused("derived value differs from formula: " + key)
            try:
                lower, upper = interval(tree)
            except (ArithmeticError, RecursionError) as error:
                raise Refused("invalid derived bound") from error
            if (
                f["bound"] == "upper"
                and (not math.isfinite(upper) or upper > f["value"])
            ) or (
                f["bound"] == "lower"
                and (not math.isfinite(lower) or lower < f["value"])
            ):
                raise Refused("derivation does not prove the declared bound: " + key)

        elif "derivation" in f:
            raise Refused("only derived facts may carry formulas")
        visiting.remove(key)
        visited.add(key)

    for key in facts:
        check(key)
    from .fact_coverage import validate_fact_scopes

    validate_fact_scopes(record)
    return record


def default_root():
    explicit = os.environ.get("ESO_MATERIAL_STORE")
    if explicit:
        return Path(explicit).expanduser().resolve()
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/eso/materials"
    if os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
            / "eso/materials"
        )
    return (
        Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        / "eso/materials"
    )


class MaterialStore:
    def __init__(self, root=None):
        self.root = Path(root).expanduser().resolve() if root else default_root()

    def _ensure(self):
        parent = self.root
        while not parent.exists():
            parent = parent.parent
        try:
            repo = subprocess.run(
                ["git", "-C", str(parent), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
            )
            if repo.returncode == 0:
                ignored = subprocess.run(
                    [
                        "git",
                        "-C",
                        repo.stdout.strip(),
                        "check-ignore",
                        "-q",
                        str(self.root),
                    ],
                    capture_output=True,
                )
                if ignored.returncode:
                    raise Refused(
                        "material store inside repository must be git-ignored"
                    )
        except FileNotFoundError:
            pass
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _path(self, sha, category):
        if not isinstance(sha, str) or not re.fullmatch("[a-f0-9]{64}", sha):
            raise Refused("invalid material/document hash")
        return self.root / category / (sha + ".json")

    def add(self, record, document):
        validate_record(record)
        path = Path(document).expanduser().resolve()
        if not path.is_file() or file_digest(path) != record["datasheet"]["sha256"]:
            raise Refused("document content does not match reviewed hash")
        self._ensure()
        sha = digest(record)
        dest = self._path(sha, "records")
        dest.parent.mkdir(exist_ok=True)
        # Exclusive creation never overwrites an immutable record.
        try:
            import json

            with dest.open("x", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(record, ensure_ascii=False, allow_nan=False, indent=2)
                    + "\n"
                )
        except FileExistsError:
            if load(dest) != record:
                raise Refused("corrupt immutable cache record")
        dump(
            self._path(record["datasheet"]["sha256"], "documents"), {"path": str(path)}
        )
        return sha

    def get(self, sha):
        path = self._path(sha, "records")
        if not path.is_file():
            raise Refused("pinned material record unavailable: " + sha)
        record = load(path)
        if digest(record) != sha:
            raise Refused("immutable material record corrupted")
        validate_record(record)
        return record

    def withdrawal(self, sha):
        path = self._path(sha, "withdrawals")
        return load(path) if path.is_file() else None

    def check(self, sha, record):
        if digest(record) != sha or self.get(sha) != record:
            raise Refused("pinned material snapshot differs")
        revoked = self.withdrawal(sha)
        if revoked:
            raise Refused("withdrawn material: " + str(revoked))
        if record.get("status") == "draft":
            raise Refused(
                "draft material requires review: " + str(record["missing_evidence"])
            )
        return self.document(record["datasheet"]["sha256"])

    def document(self, sha):
        locator = self._path(sha, "documents")
        if not locator.is_file():
            raise Refused("document locator missing: " + sha)
        path = Path(load(locator)["path"])
        if not path.is_absolute() or not path.is_file() or file_digest(path) != sha:
            raise Refused("document missing or content replaced: " + sha)
        return str(path)

    def withdraw(self, sha, reason):
        self.get(sha)
        if not isinstance(reason, str) or not reason.strip():
            raise Refused("withdrawal reason required")
        self._ensure()
        if not self.withdrawal(sha):
            dump(
                self._path(sha, "withdrawals"),
                {"reason": reason, "withdrawn_at": now()},
            )
        return self.withdrawal(sha)

    def list(self, **identity):
        rows = []
        for path in sorted((self.root / "records").glob("*.json")):
            record = self.get(path.stem)
            if all(record.get(k) == v for k, v in identity.items() if v is not None):
                rows.append(
                    {
                        "record_sha256": path.stem,
                        "status": record.get("status", "reviewed"),
                        "record": record,
                        "withdrawal": self.withdrawal(path.stem),
                    }
                )
        return rows


def import_manifest(manifest, store=None):
    store = store or MaterialStore()
    row_schema = obj(
        {
            "id": TEXT,
            **{k: TEXT for k in ("manufacturer", "model", "package")},
            "document": TEXT,
            "record_sha256": SHA,
        },
        ("id", "manufacturer", "model", "package", "document"),
    )
    shape(
        manifest,
        obj(
            {
                "materials": {"type": "array", "items": row_schema},
                "circuits": {"type": "array", "items": {"type": "object"}},
            },
            ("materials", "circuits"),
        ),
    )
    materials = []
    issues = []
    seen = set()
    for row in manifest["materials"]:
        if row["id"] in seen:
            raise Refused("duplicate manifest material ID")
        seen.add(row["id"])
        try:
            path = Path(row["document"]).expanduser().resolve()
            sha = file_digest(path)
            matches = [
                r
                for r in store.list(
                    **{k: row[k] for k in ("manufacturer", "model", "package")}
                )
                if not r["withdrawal"]
                and r["record"]["datasheet"]["sha256"] == sha
                and (
                    "record_sha256" not in row
                    or r["record_sha256"] == row["record_sha256"]
                )
            ]
            drafts = [
                item for item in matches if item["record"].get("status") == "draft"
            ]
            matches = [
                item for item in matches if item["record"].get("status") != "draft"
            ]
            if len(matches) != 1:
                issues.append(
                    {
                        "id": row["id"],
                        "reason": "missing or ambiguous reviewed material",
                        "document_sha256": sha,
                        "draft_candidates": [
                            {
                                "record_sha256": item["record_sha256"],
                                "missing_evidence": item["record"]["missing_evidence"],
                            }
                            for item in drafts
                        ],
                        "candidates": [r["record_sha256"] for r in matches],
                    }
                )
                continue
            chosen = matches[0]
            store.add(chosen["record"], path)
            materials.append(
                {
                    "id": row["id"],
                    "record_sha256": chosen["record_sha256"],
                    "record": deepcopy(chosen["record"]),
                }
            )
            issues.append(
                {
                    "id": row["id"],
                    "reason": "library identity and pins require current review",
                }
            )
        except (OSError, ValueError) as error:
            issues.append({"id": row["id"], "reason": str(error)})
    return {
        "ready": False,
        "knowledge": {
            "schema_version": 2,
            "materials": materials,
            "circuits": deepcopy(manifest["circuits"]),
        },
        "issues": issues,
        "state": "draft; assess full requirements before creating a contract",
    }
