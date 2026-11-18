"""Subprocess adapter to the separately installed upstream CLI. No shell, no drawing."""
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from .core import Refused, dump, file_digest, now


def result(envelope):
    if not isinstance(envelope, dict) or envelope.get("ok") is not True or not isinstance(envelope.get("result"), dict):
        raise Refused("missing successful typed result envelope")
    return envelope["result"]


class EasyEDA:
    def __init__(self, binary="easyeda", evidence=None, *, window=None):
        found = shutil.which(binary)
        if not found:
            raise Refused("easyeda CLI missing: install upstream CLI or pass --easyeda /absolute/path")
        self.binary = str(Path(found).resolve())
        self.evidence = Path(evidence) if evidence else None
        self.window = window
        self.counter = 0

    def run(self, args, *, target=None, json_output=True, timeout=90, check=True):
        args = list(map(str, args))
        allowed = {'sch', 'lib', 'project', 'doc', 'bom', 'api', 'actions', 'health', 'version', '--version'}
        schematic_call = len(args) >= 2 and args[0] == 'call' and args[1].startswith('schematic.')
        if not args or (args[0] not in allowed and not schematic_call):
            raise Refused('schematic-only adapter refuses commands outside schematic scope')
        argv = [self.binary, *args]
        if target:
            argv += ["--project", target["project_uuid"], "--doc", target["document_uuid"]]
            if self.window:
                argv += ["--window", self.window]
        env = dict(os.environ, EASYEDA_NO_AUTOSTART="1")
        self.counter += 1
        search_input = None
        if args[:2] in (['sch', 'lib-layout'], ['sch', 'layout-plan']) and args.count('--from') == 1:
            index = args.index('--from') + 1
            if index < len(args) and Path(args[index]).is_file():
                search_input = (Path(args[index]), file_digest(args[index]))
        started = now()
        label = started.replace(":", "").replace(".", "") + "-" + uuid.uuid4().hex
        try:
            p = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", env=env, timeout=timeout)
            receipt = {"started_at": started, "argv": argv, "exit_code": p.returncode,
                       "stdout": p.stdout, "stderr": p.stderr, "completed_at": now()}
        except subprocess.TimeoutExpired as e:
            receipt = {"started_at": started, "argv": argv, "exit_code": None, "timeout": True,
                       "stdout": (e.stdout or b"").decode(errors="replace") if isinstance(e.stdout, bytes) else e.stdout,
                       "stderr": (e.stderr or b"").decode(errors="replace") if isinstance(e.stderr, bytes) else e.stderr}
            if search_input is not None:
                receipt['input_sha256'] = search_input[1]
            if self.evidence:
                dump(self.evidence / f"{label}-{self.counter:03d}-{args[0]}-timeout.json", receipt)
            raise Refused("CLI timed out: write may have landed; STOP and read back before any retry") from e
        if search_input is not None:
            receipt['input_sha256'] = search_input[1]
            if not search_input[0].is_file() or file_digest(search_input[0]) != search_input[1]:
                receipt['input_changed'] = True
                if self.evidence:
                    dump(self.evidence / (label + '-input-changed.json'), receipt)
                raise Refused('offline search input changed while the tool was running')
        if self.evidence:
            # A per-call identity also preserves receipts from concurrent processes.
            dump(self.evidence / f"{label}-{self.counter:03d}-{args[0]}.json", receipt)
        if check and p.returncode:
            raise Refused(f"upstream exited {p.returncode}: {p.stderr.strip() or p.stdout[-1000:]}")
        if not json_output:
            return receipt
        try:
            return json.loads(p.stdout)
        except json.JSONDecodeError as e:
            raise Refused(f"upstream did not return valid JSON for {args}") from e

    def discover(self):
        version = self.run(["--version"], json_output=False)["stdout"].strip()
        actions = self.run(["actions"])
        health = self.run(["health"], check=False)
        compose = self.run(["sch", "compose", "--help"], json_output=False)["stdout"]
        schema_probe = self.run(["sch", "compose", "schema"], json_output=False, check=False)
        return {"version": version, "actions": actions, "health": health,
                "compose_help": compose, "compose_schema_supported": schema_probe["exit_code"] == 0,
                "level": "offline-discovery; health windows are live observations"}

    def guard(self, target):
        import re
        if not re.fullmatch(r"ESO-TEST-[A-Za-z0-9_-]+", target.get("project_name", "")):
            raise Refused("MVP only writes to explicit ESO-TEST-* projects")
        if not target.get("project_uuid") or not target.get("document_uuid"):
            raise Refused("exact project and schematic document UUIDs required")
        p = self.run(["project", "info"], target=target)
        info = result(p)
        if info.get("uuid") != target["project_uuid"] or info.get("friendlyName") != target["project_name"]:
            raise Refused("live project identity differs from confirmed target")
        doc = self.run(["project", "doc"], target=target)
        context, value = doc.get("context", {}), result(doc)
        if (value.get("uuid") != target["document_uuid"] or context.get("projectUuid") != target["project_uuid"]
                or value.get("documentType") != "schematic"):
            raise Refused("active schematic document differs from confirmed target")
        return doc

    def snapshot(self, target):
        self.guard(target)
        doc = self.run(["sch", "list", "--include-device-identity", "--include-bbox", "--include-pins",
                        "--include-wires", "--include-page-primitives"], target=target)
        result(doc)
        context = doc.get("context", {})
        if context.get("projectUuid") != target["project_uuid"] or context.get("documentUuid") != target["document_uuid"]:
            raise Refused("snapshot came from a different target")
        return doc
