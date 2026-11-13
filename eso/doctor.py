"""Read-only readiness checks. Connection success never implies circuit acceptance."""

import os
import shutil
import sys
import tempfile
from pathlib import Path
from .core import Refused, load
from .adapter import EasyEDA, result


def doctor(binary="easyeda", *, live=False, target=None, run_dir=None, window=None):
    checks = {}

    def record(key, status, detail):
        checks[key] = {"status": status, "detail": detail}

    def probe(key, fn):
        try:
            record(key, "passed", fn())
        except (OSError, ValueError, KeyError, TypeError) as error:
            record(key, "failed", str(error))

    record(
        "python",
        "passed" if sys.version_info >= (3, 10) else "failed",
        sys.version.split()[0],
    )

    def local():
        from .knowledge import catalog, check_catalog
        from .core import validate

        validate({"schema_version": 1}, "requirements")
        return check_catalog(catalog())

    probe("local_data", local)

    def renderer():
        from .native_text import renderer_path, render

        helper = renderer_path()
        with tempfile.TemporaryDirectory(prefix="eso-doctor-") as directory:
            svg = Path(directory) / "probe.svg"
            svg.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg"><text id="probe" x="0" y="0">ESO</text></svg>'
            )
            measured = render(svg)
            if not any(t.get("id") == "probe" for t in measured.get("texts", [])):
                raise Refused("renderer did not measure probe text")
        return {"helper": str(helper), "measurement": "synthetic SVG only"}

    probe("renderer", renderer)
    adapter = None
    if shutil.which(binary):
        adapter = EasyEDA(binary, window=window)
        probe(
            "upstream",
            lambda: adapter.run(["--version"], json_output=False, timeout=15)[
                "stdout"
            ].strip(),
        )
    else:
        record(
            "upstream", "unavailable", "安装上游 CLI 或显式指定 --easyeda；不会自动安装"
        )
    record("connection", "not_checked", "使用 --live 进行只读连接检查")
    record("snapshot", "not_checked", "需要 --live 和明确 --target；不自动选择工程")
    record(
        "upstream_compile",
        "not_checked",
        "真实 snapshot 仍需现有 prepare 的 compose/dry-run 校验；v1.16.0 已知十一类快照兼容问题",
    )
    record("acceptance", "not_checked", "没有指定对应工程的历史验收目录")
    if live and adapter:

        def connection():
            health = adapter.run(["health"], check=False, timeout=20)
            if health.get("status") != "found" or not health.get("found", {}).get(
                "raw", {}
            ).get("windows"):
                raise Refused("no connected EDA window")
            if health.get("versionGate", {}).get("verdict") not in ("ok",):
                raise Refused(
                    "upstream version gate is not ok: " + str(health.get("versionGate"))
                )
            return {
                "versions": health.get("versionGate"),
                "host": health.get("hostCompatibility"),
            }

        probe("connection", connection)
        if target:

            def snapshot():
                raw = adapter.snapshot(target)
                data = result(raw)
                page = data.get("pagePrimitives")
                classes = {
                    "components",
                    "wires",
                    "buses",
                    "arcs",
                    "circles",
                    "rectangles",
                    "polygons",
                    "texts",
                    "attributes",
                    "objects",
                }
                if (
                    not isinstance(page, dict)
                    or set(page) not in (classes, classes | {"pins"})
                    or any(not isinstance(v, list) for v in page.values())
                ):
                    raise Refused("snapshot class inventory unavailable/incomplete")
                if (
                    data.get("pinNetsAvailable") is not True
                    or data.get("wiresAvailable") is not True
                ):
                    raise Refused("snapshot connectivity observations unavailable")
                return {
                    "classes": sorted(page),
                    "level": "live-read-only; not compose compatibility or acceptance",
                }

            probe("snapshot", snapshot)
    if live and not adapter:
        record("connection", "unavailable", "缺少上游 CLI，未尝试连接")
    if run_dir:

        def acceptance():
            if not target:
                raise Refused("historical acceptance requires explicit target")
            from .cli import delivery_eligibility

            root = Path(run_dir)
            contract = load(root / "contract.json")
            if contract.get("target") != target:
                raise Refused("historical evidence belongs to another target")
            report = load(root / "verification.json")
            eligible, issues = delivery_eligibility(root, report)
            if not eligible:
                raise Refused(str(issues))
            return {
                "state": "historical-live-verified",
                "fresh_readback": False,
                "attempt": report["attempt"],
            }

        probe("acceptance", acceptance)
    return {
        "checks": checks,
        "offline_ready": all(
            checks[k]["status"] == "passed"
            for k in ("python", "local_data", "renderer")
        ),
        "live_verified": False,
        "writes_to_eda": False,
        "note": "历史验收不代表当前页面状态；prepare 与正式执行仍需全部门禁。",
    }
