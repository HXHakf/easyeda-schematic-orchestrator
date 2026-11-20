import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from eso.core import Refused, dump, file_digest, load
from eso.netlist_review import review_netlist


def sample(ground_net):
    return {'version': '2.0.0', 'projectId': 'synthetic',
            'components': {'test-u1': {'props': {'Designator': 'U1', 'Unique ID': 'test-u1'},
                           'pinInfoMap': {
                               '1': {'number': '1', 'name': 'VCC', 'net': 'PWR'},
                               '2': {'number': '2', 'name': 'GND', 'net': ground_net}}}}}


class NetlistReviewTests(unittest.TestCase):
    def test_requires_native_file_and_does_not_start_without_reviewer(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(Refused, 'native JLCEDA'):
                review_netlist(root / 'design.net', root / 'output')
            netlist = root / 'design.enet'
            dump(netlist, sample('GND'))
            with self.assertRaisesRegex(Refused, 'checkout unavailable'):
                review_netlist(netlist, root / 'output', skill=root / 'missing')
            self.assertFalse((root / 'output').exists())

    @unittest.skipUnless(os.environ.get('ESO_TEST_OFFICIAL_REVIEW') and shutil.which('node'),
                         'requires the pinned official checkout and Node')
    def test_cli_detects_short_and_binds_exact_netlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for ground_net, expected_p0 in [('GND', 0), ('PWR', 1)]:
                with self.subTest(ground_net=ground_net):
                    input_file = root / f'{ground_net}.enet'
                    dump(input_file, sample(ground_net))
                    output = root / ground_net
                    invoke = [sys.executable, '-m', 'eso.cli', 'review-netlist', '--netlist', str(input_file),
                              '--out', str(output), '--review-skill', os.environ['ESO_TEST_OFFICIAL_REVIEW']]
                    result = json.loads(subprocess.check_output(invoke, text=True))
                    self.assertEqual(result['severity_counts']['P0'], expected_p0)
                    self.assertEqual(result['netlist_sha256'], file_digest(input_file))
                    self.assertEqual(result, load(output / 'eso-review.json'))
                    lint = Path(result['findings'])
                    self.assertEqual(result['lint_sha256'], file_digest(lint))
                    self.assertEqual(sum(f['ruleId'] == 'R029' for f in load(lint)['findings']), expected_p0)
                    rerun = subprocess.run(invoke, capture_output=True)
                    self.assertEqual(rerun.returncode, 2)
                    self.assertEqual(result['lint_sha256'], file_digest(lint))
