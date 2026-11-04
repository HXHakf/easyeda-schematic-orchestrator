import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))

import netlist_diff  # noqa: E402


def make_enet(pins_by_ref):
    """pins_by_ref: {"U1": {"1": "", "2": "GDR"}, ...} -> .enet document."""
    comps = {}
    for i, (ref, pins) in enumerate(sorted(pins_by_ref.items())):
        comps[f"uid{i}"] = {
            "props": {"Designator": ref},
            "pinInfoMap": {
                num: {"number": num, "name": num, "net": net} for num, net in pins.items()
            },
        }
    return {"version": "1.0", "components": comps}


class NetlistDiffTests(unittest.TestCase):
    def diff(self, pins, expected):
        nets, pin_net = netlist_diff.actual_maps(make_enet(pins))
        return netlist_diff.diff(expected, nets, pin_net)

    def test_pass_when_pin_sets_match(self):
        report = self.diff(
            {"U1": {"2": "GDR", "3": "RESE"}, "Q1": {"1": "GDR"}, "R2": {"1": "RESE"}},
            {"nets": {"GDR": ["Q1:1", "U1:2"], "RESE": ["R2:1", "U1:3"]}, "nc": []},
        )
        self.assertTrue(report["ok"])
        self.assertEqual(report["summary"], "PASS")

    def test_missing_and_extra_pin_fail(self):
        report = self.diff(
            {"U1": {"2": "GDR", "3": ""}, "Q1": {"1": "GDR"}},
            {"nets": {"GDR": ["Q1:1", "U1:2", "R1:1"]}, "nc": ["U1:3"]},
        )
        self.assertFalse(report["ok"])
        self.assertEqual(report["nets"]["GDR"]["missing"], ["R1:1"])
        self.assertEqual(report["nc"]["missingNc"], [])

    def test_unexpected_net_fails(self):
        report = self.diff({"U1": {"2": "TYPO_NET"}}, {"nets": {"GDR": ["U1:2"]}, "nc": []})
        self.assertFalse(report["ok"])
        self.assertIn("TYPO_NET", report["unexpectedNets"])

    def test_expected_nc_not_connected(self):
        report = self.diff({"U1": {"1": "GND"}}, {"nets": {"GND": ["U1:1"]}, "nc": ["U1:1"]})
        self.assertFalse(report["ok"])
        self.assertEqual(report["nc"]["missingNc"], ["U1:1"])

    def test_allow_swap_tolerates_nonpolar_pin_permutation(self):
        pins = {"C3": {"1": "VGH", "2": "GND"}, "D3": {"1": "VGH"}}
        expected = {"nets": {"VGH": ["C3:2", "D3:1"], "GND": ["C3:1"]}, "nc": [], "allow_swap": ["C3"]}
        self.assertTrue(self.diff(pins, expected)["ok"])

    def test_swap_ref_not_in_allow_list_fails(self):
        pins = {"C3": {"1": "VGH", "2": "GND"}, "D3": {"1": "VGH"}}
        expected = {"nets": {"VGH": ["C3:2", "D3:1"], "GND": ["C3:1"]}, "nc": []}
        self.assertFalse(self.diff(pins, expected)["ok"])

    def test_cli_exit_codes_and_report_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            enet = tmp / "page.enet"
            enet.write_text(json.dumps(make_enet({"U1": {"2": "GDR"}, "Q1": {"1": "GDR"}})), encoding="utf-8")
            expect = tmp / "expected.json"
            expect.write_text(json.dumps({"nets": {"GDR": ["Q1:1", "U1:2"]}, "nc": []}), encoding="utf-8")
            report = tmp / "report.json"
            ok = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "netlist_diff.py"),
                 "--enet", str(enet), "--expect", str(expect), "--out", str(report), "--quiet"],
                capture_output=True, text=True,
            )
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["summary"], "PASS")

            expect.write_text(json.dumps({"nets": {"GDR": ["Q1:1"]}, "nc": []}), encoding="utf-8")
            bad = subprocess.run(
                [sys.executable, str(SKILL / "scripts" / "netlist_diff.py"),
                 "--enet", str(enet), "--expect", str(expect), "--quiet"],
                capture_output=True, text=True,
            )
            self.assertEqual(bad.returncode, 1)


if __name__ == "__main__":
    unittest.main()
