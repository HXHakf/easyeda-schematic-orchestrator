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
