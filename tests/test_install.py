"""Installer safety checks; no pip network or EDA calls in unit tests."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import sys
import unittest
import venv
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("eso_installer", ROOT / "scripts/install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


class InstallTests(unittest.TestCase):
    def paths(self, root):
        return root / "skills/eso", root / "runtime", root / "bin"

    def test_dry_run_does_not_create_files_or_processes(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(installer.subprocess, "run") as run:
            root = Path(directory)
            result = installer.install(ROOT, *self.paths(root), dry_run=True)
            self.assertEqual(result["status"], "dry-run")
            self.assertEqual(list(root.iterdir()), [])
            run.assert_not_called()

    def test_chinese_demo_and_contract_work_in_non_utf8_locale(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, LC_ALL="C", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", PYTHONIOENCODING="ascii")
            commands = [
                [sys.executable, str(ROOT / "scripts/demo_workflow.py"), "--out", str(root / "demo")],
                [sys.executable, str(ROOT / "scripts/eso.py"), "contract", str(ROOT / "examples/requirements.json"), "--out", str(root / "contract.json")],
                [sys.executable, str(ROOT / "scripts/eso.py"), "assess", str(ROOT / "examples/requirements.json")]]
            for command in commands:
                result = subprocess.run(command, env=env, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
            self.assertIn("设计", (root / "contract.md").read_text(encoding="utf-8"))
            self.assertTrue(json.loads(result.stdout.decode("utf-8"))["ready"])

    def test_unmanaged_skill_and_runtime_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            skill, runtime, bin_dir = self.paths(Path(directory))
            skill.mkdir(parents=True)
            (skill / "important").write_text("keep")
            with self.assertRaisesRegex(ValueError, "unmanaged skill"):
                installer.install(ROOT, skill, runtime, bin_dir, dry_run=True)
            runtime.mkdir()
            (runtime / "important").write_text("keep runtime")
            with self.assertRaisesRegex(ValueError, "unmanaged runtime"):
                installer.install(ROOT, skill, runtime, bin_dir, replace=True, dry_run=True)
            self.assertEqual((skill / "important").read_text(), "keep")
            self.assertEqual((runtime / "important").read_text(), "keep runtime")

    def test_nested_installation_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "must be separate"):
                installer.install(ROOT, root / "skill", root / "skill/runtime", root / "bin", dry_run=True)

    def test_replace_backs_up_outside_discovered_skills_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            skill, runtime, bin_dir = self.paths(Path(directory))
            skill.mkdir(parents=True)
            (skill / "personal-note").write_text("original")
            python = runtime / "venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            (runtime / installer.MARKER).write_text('{"owner":"' + installer.OWNER + '"}')
            with patch.object(installer.subprocess, "run"):
                first = installer.install(ROOT, skill, runtime, bin_dir, replace=True)
                second = installer.install(ROOT, skill, runtime, bin_dir)
            self.assertEqual((Path(first["backup"]) / "personal-note").read_text(), "original")
            self.assertTrue(Path(first["backup"]).is_relative_to(runtime))
            self.assertIsNone(second["backup"])
            self.assertEqual(first["source_sha256"], second["source_sha256"])
            self.assertTrue((skill / "schemas/plan.json").is_file())
            self.assertFalse((skill / ".git").exists())
            self.assertFalse((skill / "runs").exists())

    def test_pip_failure_keeps_old_skill_and_launcher(self):
        with tempfile.TemporaryDirectory() as directory:
            skill, runtime, bin_dir = self.paths(Path(directory))
            skill.mkdir(parents=True)
            (skill / "old").write_text("original")
            bin_dir.mkdir()
            launcher = bin_dir / "eso"
            launcher.write_text("original launcher")
            python = runtime / "venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            (runtime / installer.MARKER).write_text('{"owner":"' + installer.OWNER + '"}')
            with patch.object(installer.subprocess, "run", side_effect=subprocess.CalledProcessError(1, "pip")):
                with self.assertRaises(subprocess.CalledProcessError):
                    installer.install(ROOT, skill, runtime, bin_dir, replace=True)
            self.assertEqual((skill / "old").read_text(), "original")
            self.assertEqual(launcher.read_text(), "original launcher")

    def test_installed_skill_includes_usable_layout_guidance_and_auditors(self):
        with tempfile.TemporaryDirectory() as directory:
            skill, runtime, bin_dir = self.paths(Path(directory))
            python = runtime / "venv/bin/python"
            python.parent.mkdir(parents=True)
            python.touch()
            (runtime / installer.MARKER).write_text('{"owner":"' + installer.OWNER + '"}')
            with patch.object(installer.subprocess, "run"):
                installer.install(ROOT, skill, runtime, bin_dir)
            layout = skill / "extras/schematic-verified-layout"
            for name in ("SKILL.md", "references/drawing-grammar.md",
                         "scripts/circuit_audit.py", "scripts/readability_audit.py"):
                self.assertTrue((layout / name).is_file(), name)
            # Exercise the copied compatibility API without relying on checkout imports.
            probe = ("import importlib.util,sys; "
                     "s=importlib.util.spec_from_file_location('renderer',sys.argv[1]); "
                     "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
                     "assert callable(m.measure); "
                     "assert 'svg' in m.prepare_svg('<svg xmlns=\"http://www.w3.org/2000/svg\"/>')")
            result = subprocess.run([sys.executable, "-I", "-c", probe,
                                     str(layout / "scripts/measure_svg_text.py")],
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_launcher_uses_installed_module_from_other_working_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            venv.EnvBuilder(with_pip=False).create(runtime)
            python = runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            purelib = subprocess.check_output([
                str(python), "-I", "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"
            ], text=True).strip()
            locations = {
                "installed": Path(purelib),
                "cwd": root / "project",
                "environment": root / "extra-modules",
            }
            for name, location in locations.items():
                package = location / "eso"
                package.mkdir(parents=True)
                (package / "__init__.py").write_text("")
                (package / "cli.py").write_text(
                    "import json,sys\nprint(json.dumps({'source': " + repr(name)
                    + ", 'args': sys.argv[1:]}))\n"
                )
            launcher = root / ("eso.cmd" if os.name == "nt" else "eso")
            launcher.write_text(installer.launcher_text(python))
            if os.name != "nt":
                launcher.chmod(0o755)
            command = [str(launcher), "a b", "--json"]
            if os.name == "nt":
                command = 'cmd /d /s /c "' + subprocess.list2cmdline(command) + '"'
            environment = dict(os.environ, PYTHONPATH=str(locations["environment"]))
            for cwd in (locations["cwd"], root):
                with self.subTest(cwd=cwd):
                    result = subprocess.run(command, cwd=cwd, env=environment,
                                            capture_output=True, text=True, check=True)
                    self.assertEqual(json.loads(result.stdout), {
                        "source": "installed", "args": ["a b", "--json"]
                    })

    @unittest.skipUnless(os.environ.get('EASYEDA_BIN') and __import__('shutil').which('uv'), 'requires cached wheel builder and offline CLI')
    def test_real_wheel_isolated_primary_candidate_and_modern_delivery(self):
        import shutil
        from eso.core import dump, load
        from test_main_path_fixes import prepared_inputs
        from test_drawing_review import drawing_run
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            for name in ('eso', 'schemas', 'extras'):
                shutil.copytree(ROOT / name, source / name, ignore=shutil.ignore_patterns('__pycache__'))
            for name in ('pyproject.toml', 'README.md', 'LICENSE'):
                shutil.copy2(ROOT / name, source / name)
            wheels = root / 'wheels'
            built = subprocess.run(['uv', 'build', '--offline', '--wheel', '--out-dir', str(wheels), str(source)],
                                   capture_output=True, text=True)
            self.assertEqual(built.returncode, 0, built.stderr)
            runtime = root / 'runtime'
            shutil.copytree(sys.prefix, runtime, symlinks=True)
            python = runtime / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            wheel = next(wheels.glob('*.whl'))
            installed = subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps',
                                        '--force-reinstall', str(wheel)], capture_output=True, text=True, cwd=root)
            self.assertEqual(installed.returncode, 0, installed.stderr)
            for name in ('circuit_audit.py', 'readability_audit.py'):
                self.assertTrue((runtime / 'share/eso/auditors' / name).is_file())
            script = root / 'offline_cli.py'
            script.write_text('''import json,sys
from pathlib import Path
from unittest.mock import patch
import eso
from eso.adapter import EasyEDA
from eso.cli import main
from eso.core import load
assert Path(eso.__file__).is_relative_to(Path(sys.prefix))
from eso.main_paths import audit_tools
assert all(Path(m.__file__).is_relative_to(Path(sys.prefix)/'share/eso/auditors') for m in audit_tools())
base=Path(sys.argv[1])
calls=[]
class Offline(EasyEDA):
    def guard(self,target): pass
    def snapshot(self,target): return load(base/'before.json')
    def run(self,args,**kw):
        calls.append(list(map(str,args)))
        assert args[:2]==['sch','compose'] or (args[:2]==['sch','apply'] and '--dry-run' in args)
        return super().run(args,**kw)
try:
    with patch('eso.cli.EasyEDA',Offline): code=main(sys.argv[2:])
finally:
    (base/'offline-calls.json').write_text(json.dumps(calls))
raise SystemExit(code)
''')
            for defect in (None, 'marker-branch', 'local-open'):
                candidate = root / ('candidate-' + str(defect))
                candidate.mkdir()
                prepared_inputs(candidate, defect)
                args = ['--easyeda', os.environ['EASYEDA_BIN'], 'prepare',
                        '--contract', str(candidate/'contract.json'), '--approval', str(candidate/'approval.json'),
                        '--plan', str(candidate/'plan.json'), '--composition', str(candidate/'source.json'),
                        '--before', str(candidate/'before.json'), '--run-dir', str(candidate/'run')]
                result = subprocess.run([str(python), '-I', str(script), str(candidate), *args],
                                        capture_output=True, text=True, cwd=root)
                self.assertEqual(result.returncode, 0 if defect is None else 2, result.stderr + result.stdout)
                self.assertEqual((candidate/'run/bundle.json').exists(), defect is None)
                calls = load(candidate/'offline-calls.json')
                if defect is not None:
                    self.assertFalse(any(call[:2] == ['sch','apply'] for call in calls), calls)
            delivery = root/'delivery'
            delivery.mkdir()
            workflow, reviews = drawing_run(delivery, modern=True)
            result = subprocess.run([str(python), '-I', '-m', 'eso.cli', 'deliver', '--run-dir', str(workflow.root),
                                     '--reviews', str(reviews), '--out', str(delivery/'installed-manifest.json')],
                                    capture_output=True, text=True, cwd=root)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(load(delivery/'installed-manifest.json')['state'], 'verified')

    @unittest.skipIf(installer.os.name == "nt", "POSIX quoting check")
    def test_launcher_quotes_runtime_paths_and_preserves_arguments(self):
        with tempfile.TemporaryDirectory(prefix="eso space ' ") as directory:
            root = Path(directory)
            python = root / "fake-python"
            python.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            python.chmod(0o755)
            launcher = root / "eso"
            launcher.write_text(installer.launcher_text(python))
            launcher.chmod(0o755)
            result = subprocess.run([str(launcher), "a b", "$(false)"], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.splitlines(), ["-I", "-m", "eso.cli", "a b", "$(false)"])
            with self.assertRaisesRegex(ValueError, "unsupported"):
                installer.launcher_text(Path("C:/percent%/python.exe"), windows=True)


if __name__ == "__main__":
    unittest.main()
