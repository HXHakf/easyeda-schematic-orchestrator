import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from test_knowledge_v2 import v2_fixture
from eso.core import dump


class KnowledgeToolsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = patch.dict(os.environ, {"ESO_MATERIAL_STORE": str(self.root / "cache")})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "eso.cli", *map(str, args)],
            capture_output=True,
            text=True,
        )

    def test_doctor_without_upstream_reports_unavailable_not_live_pass(self):
        p = self.cli("--easyeda", "eso-nonexistent-test", "doctor")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertEqual(d["checks"]["upstream"]["status"], "unavailable")
        self.assertEqual(d["checks"]["connection"]["status"], "not_checked")
        self.assertEqual(d["checks"]["acceptance"]["status"], "not_checked")
        self.assertFalse(d["live_verified"])

    def test_cache_cli_lists_withdraws_and_rejects_corrupt_document(self):
        r = v2_fixture(self.root)
        m = r["knowledge"]["materials"][0]
        inp = self.root / "record.json"
        dump(inp, m["record"])
        p = self.cli("kb-material-add", inp, "--document", self.root / "switch.txt")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["record_sha256"], m["record_sha256"])
        p = self.cli(
            "kb-material-withdraw", m["record_sha256"], "--reason", "incorrect pin"
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        p = self.cli("kb-material-list", "--model", "SYNTHETIC-switch")
        self.assertTrue(json.loads(p.stdout)["materials"][0]["withdrawal"])
        (self.root / "switch.txt").write_text("replaced")
        self.assertEqual(
            self.cli(
                "kb-material-add", inp, "--document", self.root / "switch.txt"
            ).returncode,
            2,
        )

    def test_renderer_is_bundled_and_explicit_browser_override_is_respected(self):
        from eso.native_text import renderer_path

        self.assertTrue(renderer_path().is_file())
        import importlib.util

        spec = importlib.util.spec_from_file_location("renderer", renderer_path())
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        browser = self.root / "browser"
        browser.write_text("test executable locator")
        browser.chmod(0o700)
        with patch.dict(os.environ, {"ESO_BROWSER": str(browser)}):
            self.assertEqual(mod.find_browser(), str(browser))
        with patch.dict(os.environ, {"ESO_BROWSER": str(self.root / "absent")}):
            with self.assertRaises(ValueError):
                mod.find_browser()

    def test_import_cli_produces_draft_without_library_or_approval(self):
        r = v2_fixture(self.root)
        rows = []
        for m in r["knowledge"]["materials"]:
            rows.append(
                {
                    "id": m["id"],
                    **{k: m["record"][k] for k in ("manufacturer", "model", "package")},
                    "document": str(self.root / (m["id"] + ".txt")),
                }
            )
        f = self.root / "manifest.json"
        dump(f, {"materials": rows, "circuits": r["knowledge"]["circuits"]})
        p = self.cli("kb-import", f, "--out", self.root / "draft.json")
        self.assertEqual(p.returncode, 0, p.stderr)
        d = json.loads(p.stdout)
        self.assertFalse(d["ready"])
        self.assertEqual(len(d["knowledge"]["materials"]), 4)
        self.assertFalse(any("library" in m for m in d["knowledge"]["materials"]))
        self.assertNotIn("approval", d)

    @unittest.skipUnless(
        os.environ.get("EASYEDA_BIN"), "set EASYEDA_BIN for offline compiler"
    )
    def test_live_diagnostic_reports_compiler_rejection_without_eda_writes(self):
        from eso.adapter import EasyEDA
        from eso.doctor import doctor
        from test_bridge import fixture as geometry_fixture, TARGET

        _, _, _, snapshot, _, _, _ = geometry_fixture(self.root)
        # The replacement probe is only applicable to a proven blank page.
        data = snapshot["result"]
        data["components"] = data["components"][:1]
        data["pagePrimitives"]["components"] = data["components"]
        binary = os.environ["EASYEDA_BIN"]

        class RecordedReads(EasyEDA):
            def snapshot(self, target):
                return snapshot

            def run(self, args, **kwargs):
                if args == ["health"]:
                    return {
                        "status": "found",
                        "found": {"raw": {"windows": [{"windowId": "synthetic"}]}},
                        "versionGate": {"verdict": "ok"},
                    }
                if (
                    args[0] != "--version"
                    and args[:2] != ["sch", "compose"]
                    and args[-1] != "--dry-run"
                ):
                    raise AssertionError("unexpected live/write command")
                return super().run(args, **kwargs)

        with patch("eso.doctor.EasyEDA", return_value=RecordedReads(binary)):
            report = doctor(binary, live=True, target=TARGET)
        self.assertEqual(report["checks"]["upstream_compile"]["status"], "failed")
        self.assertIn("class inventory", report["checks"]["upstream_compile"]["detail"])
        self.assertFalse(report["writes_to_eda"])
        self.assertFalse(report["live_verified"])

    def test_occupied_page_keeps_blank_replacement_probe_not_checked(self):
        import copy
        from eso.doctor import doctor
        from test_bridge import fixture as geometry_fixture, TARGET

        _, _, _, snapshot, _, _, _ = geometry_fixture(self.root)

        class Occupied:
            def snapshot(self, target):
                return snapshot

            def run(self, args, **kwargs):
                if args == ["--version"]:
                    return {"stdout": "easyeda-agent v1.17.0"}
                if args == ["health"]:
                    return {
                        "status": "found",
                        "found": {"raw": {"windows": [{"windowId": "synthetic"}]}},
                        "versionGate": {"verdict": "ok"},
                    }
                raise AssertionError("occupied page must not use replacement probe")

        for content in ("part", "text", "orphan-attribute"):
            with self.subTest(content=content):
                data = snapshot["result"]
                if content != "part":
                    data["components"] = data["components"][:1]
                    data["pagePrimitives"]["components"] = data["components"]
                data["pagePrimitives"]["texts"] = (
                    [{"primitiveId": "synthetic-note", "Content": "test"}]
                    if content == "text" else []
                )
                data["pagePrimitives"]["attributes"] = (
                    [{"primitiveId": "synthetic-orphan", "ParentPrimitiveId": "unknown"}]
                    if content == "orphan-attribute" else []
                )
                before = copy.deepcopy(snapshot)
                with patch("eso.doctor.EasyEDA", return_value=Occupied()), patch(
                    "eso.native_text.render", return_value={"texts": [{"id": "probe"}]}
                ):
                    report = doctor(sys.executable, live=True, target=TARGET)
                self.assertEqual(report["checks"]["snapshot"]["status"], "passed")
                self.assertEqual(report["checks"]["upstream_compile"]["status"], "not_checked")
                self.assertIn("blank", report["checks"]["upstream_compile"]["detail"])
                self.assertFalse(report["live_verified"])
                self.assertFalse(report["writes_to_eda"])
                self.assertEqual(snapshot, before)

    def test_connected_older_connector_is_reported_as_version_warning(self):
        from eso.doctor import doctor

        class Connected:
            def run(self, args, **kwargs):
                if args == ["--version"]:
                    return {"stdout": "easyeda-agent v1.17.0"}
                if args == ["health"]:
                    return {
                        "status": "found",
                        "found": {"raw": {"windows": [{"windowId": "synthetic"}]}},
                        "versionGate": {"verdict": "block", "connectors": ["1.16.0"]},
                    }
                raise AssertionError("unexpected command")

        with patch("eso.doctor.EasyEDA", return_value=Connected()):
            report = doctor(sys.executable, live=True)
        self.assertEqual(report["checks"]["connection"]["status"], "passed")
        self.assertEqual(report["checks"]["versions"]["status"], "warning")
        self.assertFalse(report["live_verified"])
