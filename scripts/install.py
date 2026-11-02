#!/usr/bin/env python3
"""Install this checkout as a self-contained Codex skill, without EDA access."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
import venv

NAME = "easyeda-schematic-orchestrator"
OWNER = "HXHakf/easyeda-schematic-orchestrator"
ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ("SKILL.md", "README.md", "AGENTS.md", "LICENSE", "NOTICE", "CONTRIBUTING.md",
           "pyproject.toml", "agents", "eso", "schemas", "scripts", "references", "docs", "examples", "tests")
MARKER = ".eso-install.json"


def payload_files(source):
    files = []
    for name in PAYLOAD:
        path = source / name
        if not path.exists():
            raise ValueError(f"Incomplete checkout: {name} missing")
        candidates = [path] if path.is_file() else sorted(path.rglob("*"))
        for item in candidates:
            if item.is_symlink():
                raise ValueError(f"Unexpected symbolic link: {item}")
            if item.is_file() and "__pycache__" not in item.parts and item.suffix not in (".pyc", ".pyo"):
                files.append(item.relative_to(source))
    return files


def source_hash(source, files):
    result = hashlib.sha256()
    for path in sorted(files):
        result.update(path.as_posix().encode() + b"\0" + (source / path).read_bytes() + b"\0")
    return result.hexdigest()


def read_marker(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def checked_paths(source, skill, runtime, bin_dir, replace):
    # Keep runtime/generated files outside the source and installed skill.
    # Parent/child overlap would make replacement destructive or recursive.
    for left, right in ((source, skill), (source, runtime), (source, bin_dir),
                        (skill, runtime), (skill, bin_dir), (runtime, bin_dir)):
        if left == right or left in right.parents or right in left.parents:
            raise ValueError(f"Installation paths must be separate: {left}, {right}")
    marker = read_marker(skill / MARKER)
    if skill.exists() and marker.get("owner") != OWNER and not replace:
        raise ValueError("Existing unmanaged skill; inspect it first, then use --replace to keep a backup")
    runtime_marker = read_marker(runtime / MARKER)
    if runtime.exists() and any(runtime.iterdir()) and runtime_marker.get("owner") != OWNER:
        raise ValueError("Existing unmanaged runtime; choose a new --runtime-dir (never deleted automatically)")
    launcher = bin_dir / ("eso.cmd" if os.name == "nt" else "eso")
    if launcher.exists() and OWNER not in launcher.read_text(encoding="utf-8") and not replace:
        raise ValueError("Existing unmanaged eso launcher; inspect it first or choose another --bin-dir")
    return launcher


def launcher_text(python, windows=None):
    windows = os.name == "nt" if windows is None else windows
    if windows:
        if any(c in str(python) for c in '%\r\n"'):
            raise ValueError("Windows runtime path contains unsupported command characters")
        return f'@rem {OWNER}\n@"{python}" -m eso.cli %*\n'
    return f'#!/bin/sh\n# {OWNER}\nexec {shlex.quote(str(python))} -m eso.cli "$@"\n'


def install(source, skill, runtime, bin_dir, replace=False, dry_run=False):
    files = payload_files(source)
    launcher = checked_paths(source, skill, runtime, bin_dir, replace)
    python = runtime / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    content = launcher_text(python)
    manifest = {"owner": OWNER, "source_sha256": source_hash(source, files),
                "skill": str(skill), "python": str(python), "launcher": str(launcher)}
    if dry_run:
        return {"status": "dry-run", **manifest, "writes": False,
                "network": "pip dependencies only during actual install; no EDA calls"}
    skill.parent.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    # Mark this dedicated runtime before creation, so interrupted installs can resume.
    (runtime / MARKER).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(runtime / "venv")
    subprocess.run([str(python), "-m", "pip", "install", "--disable-pip-version-check", str(source)], check=True)
    subprocess.run([str(python), "-m", "eso.cli", "--version"], check=True)
    backup = None
    with tempfile.TemporaryDirectory(prefix=".eso-stage-", dir=skill.parent) as directory:
        staged = Path(directory) / NAME
        staged.mkdir()
        for rel in files:
            dest = staged / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / rel, dest)
        (staged / MARKER).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        # Compare actual bytes, not merely the old manifest: restore modified installs.
        same = (skill / MARKER).is_file() and all(
            (skill / rel).is_file() and (skill / rel).read_bytes() == (source / rel).read_bytes()
            for rel in files)
        if not same:
            if skill.exists():
                backup = runtime / "backups" / (skill.name + "-" + uuid.uuid4().hex[:8])
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(skill), str(backup))
            try:
                staged.rename(skill)
            except OSError:
                if backup:
                    shutil.move(str(backup), str(skill))
                raise
    if launcher.exists() and OWNER not in launcher.read_text(encoding="utf-8"):
        shutil.copy2(launcher, launcher.with_name(launcher.name + ".backup-" + uuid.uuid4().hex[:8]))
    launcher.write_text(content, encoding="utf-8")
    if os.name != "nt":
        launcher.chmod(0o755)
    return {"status": "installed", **manifest, "backup": str(backup) if backup else None,
            "next": "Run launcher --version; read docs/AI-SETUP.md; no EDA project was changed"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    home = Path.home()
    parser.add_argument("--skill-dir", type=Path, default=home / ".agents/skills" / NAME)
    parser.add_argument("--runtime-dir", type=Path, default=home / ".local/share" / NAME)
    parser.add_argument("--bin-dir", type=Path, default=home / ".local/bin")
    parser.add_argument("--replace", action="store_true", help="back up an existing unmanaged skill/launcher")
    parser.add_argument("--dry-run", action="store_true", help="validate and print paths; no writes or downloads")
    args = parser.parse_args(argv)
    if sys.version_info < (3, 10):
        parser.error("Python >=3.10 is required; select that interpreter explicitly")
    raw_paths = [args.skill_dir.expanduser(), args.runtime_dir.expanduser(), args.bin_dir.expanduser()]
    if any(p.is_symlink() for p in raw_paths):
        parser.error("Use real installation directories, not symbolic links")
    try:
        result = install(ROOT, *(p.resolve() for p in raw_paths), replace=args.replace, dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f"Install refused/failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
