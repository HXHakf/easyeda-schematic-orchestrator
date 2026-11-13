"""Interrupted and concurrent cache writes use real files and synthetic records."""

import copy
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from eso.core import Refused, digest, dump
from eso.materials import MaterialStore
from test_knowledge_v2 import v2_fixture


class MaterialAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        environment = patch.dict(os.environ, {"ESO_MATERIAL_STORE": str(self.root / "cache")})
        environment.start()
        self.addCleanup(environment.stop)
        self.record = copy.deepcopy(v2_fixture(self.root)["knowledge"]["materials"][0]["record"])
        self.record["coverage_review"] += " independent atomicity case"
        self.store = MaterialStore()
        self.document = self.root / "switch.txt"

    def test_process_exit_before_publication_can_be_retried(self):
        payload = self.root / "input.json"
        dump(payload, self.record)
        script = """
import json
import os
import sys
from eso.core import load
from eso.materials import MaterialStore
record = load(sys.argv[1])
original = json.dumps
def interrupt(value, *args, **kwargs):
    if sys.argv[3] == 'encoding' and kwargs.get('indent') == 2:
        os._exit(23)
    return original(value, *args, **kwargs)
json.dumps = interrupt
replace = os.replace
def interrupt_publication(source, destination):
    if sys.argv[3] == 'publication' and str(destination).endswith(sys.argv[4]):
        os._exit(23)
    return replace(source, destination)
os.replace = interrupt_publication
MaterialStore().add(record, sys.argv[2])
"""
        path = self.root / "cache/records" / (digest(self.record) + ".json")
        for phase in ("encoding", "publication"):
            with self.subTest(phase=phase):
                result = subprocess.run([
                    sys.executable, "-c", script, str(payload), str(self.document), phase, path.name
                ])
                self.assertEqual(result.returncode, 23)
                self.assertFalse(path.exists(), "an interrupted write exposed a final cache record")
        sha = self.store.add(self.record, self.document)
        self.assertEqual(self.store.get(sha), self.record)

    def test_concurrent_identical_adds_only_expose_complete_records(self):
        encoding = threading.Event()
        release = threading.Event()
        original = json.dumps

        def pause_first_writer(value, *args, **kwargs):
            if threading.current_thread().name == "cache-first" and value == self.record:
                if kwargs.get("indent") == 2:
                    encoding.set()
                    if not release.wait(10):
                        raise TimeoutError("concurrent cache test did not release writer")
            return original(value, *args, **kwargs)

        errors = []
        results = []

        def first_writer():
            try:
                results.append(self.store.add(self.record, self.document))
            except Exception as error:
                errors.append(error)

        with patch("json.dumps", side_effect=pause_first_writer):
            thread = threading.Thread(target=first_writer, name="cache-first")
            thread.start()
            try:
                self.assertTrue(encoding.wait(10), "first writer did not reach encoding")
                try:
                    results.append(self.store.add(self.record, self.document))
                except Exception as error:
                    errors.append(error)
            finally:
                release.set()
                thread.join(10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results, [digest(self.record)] * 2)
        self.assertEqual(self.store.get(results[0]), self.record)

    def test_concurrent_withdrawals_preserve_first_published_reason(self):
        sha = self.store.add(self.record, self.document)
        initial_reads = threading.Barrier(2)
        first_published = threading.Event()
        original_withdrawal = self.store.withdrawal
        original_dump = dump
        local = threading.local()

        def simultaneous_initial_read(value):
            result = original_withdrawal(value)
            if not getattr(local, "read", False):
                local.read = True
                initial_reads.wait(10)
            return result

        def ordered_publication(path, value, **kwargs):
            if value.get("reason") == "second":
                if not first_published.wait(10):
                    raise TimeoutError("first withdrawal was not published")
            try:
                return original_dump(path, value, **kwargs)
            finally:
                if value.get("reason") == "first":
                    first_published.set()

        with patch.object(self.store, "withdrawal", side_effect=simultaneous_initial_read):
            with patch("eso.materials.dump", side_effect=ordered_publication):
                with ThreadPoolExecutor(max_workers=2) as pool:
                    first = pool.submit(self.store.withdraw, sha, "first")
                    second = pool.submit(self.store.withdraw, sha, "second")
                    first_result = first.result(timeout=15)
                    second_result = second.result(timeout=15)
        self.assertEqual(first_result["reason"], "first")
        self.assertEqual(second_result, first_result)
        self.assertEqual(self.store.withdrawal(sha), first_result)

    def test_cache_writes_work_without_hard_link_support(self):
        with patch("os.link", side_effect=OSError(errno.EOPNOTSUPP, "unsupported")):
            sha = self.store.add(self.record, self.document)
            self.assertEqual(self.store.get(sha), self.record)
            self.assertEqual(self.store.withdraw(sha, "synthetic withdrawal")["reason"], "synthetic withdrawal")

    def test_corrupt_withdrawal_record_cannot_allow_execution_checks(self):
        sha = self.store.add(self.record, self.document)
        self.store.withdraw(sha, "synthetic invalid source")
        path = self.root / "cache/withdrawals" / (sha + ".json")
        for value in ({}, {"reason": "", "withdrawn_at": "present"}, None, []):
            with self.subTest(value=value):
                dump(path, value)
                with self.assertRaises(Refused):
                    self.store.check(sha, self.record)
