#!/usr/bin/env python3
"""Run an easyeda CLI command from a JSON job file.

Why this exists: passing JSON with quotes to a native .exe through a shell
(PowerShell esp.) mangles quoting, and BOM/encoding sneaks in easily.
Writing the argv as a JSON list and invoking with an argv list avoids both.

Job file (UTF-8, no BOM):

    {"argv": ["sch", "list", "--project", "<uuid>", "--doc", "<page>",
              "--include-pins"]}

Usage:

    python edacall.py --job job.json [--exe easyeda] [--out result.json]
                      [--timeout 120] [--print stdout|none]

Exit code: 0 when the CLI exit code is 0 AND stdout parsed as JSON (or --print
stdout was requested), else the CLI exit code (or 2 for a bad job file).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys


def load_job(path: str) -> list[str]:
    with open(path, "r", encoding="utf-8-sig") as fh:
        job = json.load(fh)
    if not isinstance(job, dict) or not isinstance(job.get("argv"), list):
        raise SystemExit('job file must be {"argv": [...]}')
    argv = job["argv"]
    if not all(isinstance(a, str) for a in argv):
        raise SystemExit("every argv item must be a string")
    return argv


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--job", required=True)
    ap.add_argument("--exe", default="easyeda")
    ap.add_argument("--out", default=None)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--print", dest="do_print", choices=["stdout", "none"], default="none")
    args = ap.parse_args()

    argv = load_job(args.job)
    exe = shutil.which(args.exe) or args.exe
    if not shutil.which(args.exe):
        print(f"[edacall] warning: {args.exe!r} not on PATH, invoking as given", file=sys.stderr)

    try:
        proc = subprocess.run(
            [exe, *argv],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code = 124
        out = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        err = f"timeout after {args.timeout}s: {exc}"

    parsed = None
    stripped = out.strip()
    if stripped[:1] in "{[":
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None

    result = {
        "argv": argv,
        "exitCode": code,
        "jsonParsed": parsed is not None,
        "json": parsed,
        "stdout": out,
        "stderr": err,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)

    if args.do_print == "stdout":
        sys.stdout.write(out)
    else:
        head = stripped.splitlines()[0][:200] if stripped else "(no stdout)"
        print(f"[edacall] exit={code} json={parsed is not None} :: {head}")
    if err.strip():
        print("[edacall] stderr:", err.strip().splitlines()[0][:300], file=sys.stderr)

    if code != 0:
        return code
    if args.do_print == "none" and parsed is None:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
