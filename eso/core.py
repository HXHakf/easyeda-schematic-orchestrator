import hashlib
import errno
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class Refused(ValueError):
    """Incomplete or unsafe request; no dependent write may proceed."""


def load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Refused(f"duplicate JSON key: {key}")
            result[key] = value
        return result
    return json.loads(Path(path).read_text(encoding="utf-8"), object_pairs_hook=unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(Refused(f"invalid number {x}")))


def dump(path, data, *, exclusive=False):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=p.parent, delete=False) as f:
            temporary = f.name
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            from .locking import file_lock
            # Keep the lock inode stable across retries and concurrent publishers.
            with file_lock(p.with_name(p.name + '.lock'), blocking=True):
                if os.path.lexists(p):
                    raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(p))
                os.replace(temporary, p)
        else:
            os.replace(temporary, p)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def digest(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def validate(data, schema):
    from jsonschema import Draft202012Validator
    base = ROOT / "schemas"
    if not base.exists():
        base = Path(sys.prefix) / "share/eso/schemas"
    errors = sorted(Draft202012Validator(load(base / f"{schema}.json")).iter_errors(data),
                    key=lambda e: str(list(e.path)))
    if errors:
        raise Refused("; ".join(f"{'.'.join(map(str, e.path)) or '$'}: {e.message}" for e in errors))
    return data


def confirm(contract, supplied_hash, actor):
    validate(contract, "contract")
    from .knowledge import check_contract_knowledge
    check_contract_knowledge(contract)
    from .design_review import validate_design_evidence
    validate_design_evidence(contract)
    if digest(contract) != supplied_hash or not actor.strip():
        raise Refused("confirmation must name the human and match the exact reviewed contract hash")
    return {"schema_version": 1, "contract_sha256": supplied_hash,
            "actor": actor, "confirmed_at": now()}


def check_confirmation(contract, approval, *, evidence_root=None):
    validate(contract, "contract")
    from .knowledge import check_contract_knowledge
    check_contract_knowledge(contract, evidence_root=evidence_root)
    from .design_review import validate_design_evidence
    validate_design_evidence(contract, search_paths=(evidence_root,) if evidence_root else ())
    validate(approval, "approval")
    if approval["contract_sha256"] != digest(contract):
        raise Refused("contract changed or was not confirmed; review the new contract")
