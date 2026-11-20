"""Run the separately distributed EasyEDA rules on a local ENET export."""
import os
from collections import Counter
from pathlib import Path
import shutil
import subprocess

from .core import Refused, dump, file_digest, load

REVIEW_REVISION = 'b1b9ebe0970bf6241c2cf6f7d65acb087001a371'


def review_root():
    return Path.home() / '.local' / 'share' / 'easyeda-schematic-review'


def check_review_checkout(root):
    if not (root / '.git').exists() or not (root / 'scripts/review.mjs').is_file():
        raise Refused('official review checkout unavailable; see references/native-netlist-review.md')
    try:
        head = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                              capture_output=True, text=True, check=True, timeout=10).stdout.strip()
        status = subprocess.run(['git', '-C', str(root), 'status', '--porcelain', '--untracked-files=no'],
                                capture_output=True, text=True, check=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError) as error:
        raise Refused(f'could not inspect official review checkout: {error}') from error
    if head != REVIEW_REVISION or status:
        raise Refused(f'official review needs the clean validated checkout {REVIEW_REVISION}')


def review_netlist(netlist, output, *, skill=None):
    netlist, output = Path(netlist).resolve(), Path(output).absolute()
    if netlist.suffix.lower() != '.enet':
        raise Refused('review-netlist requires a native JLCEDA .enet file')
    data = load(netlist)
    if not isinstance(data, dict) or not isinstance(data.get('components'), dict) or not data['components']:
        raise Refused('native .enet contains no component inventory')

    root = Path(skill).expanduser().resolve() if skill else review_root()
    check_review_checkout(root)
    node = shutil.which('node')
    if node is None:
        raise Refused('official review needs Node.js 18 or later')
    output.mkdir(parents=True, exist_ok=False)
    source_hash = file_digest(netlist)
    args = [node, str(root / 'scripts/review.mjs'), 'run', '--source', 'file',
            '--netlist', str(netlist), '--out', str(output),
            '--mode', 'standard', '--no-ai', '--no-screenshots']
    env = dict(os.environ)
    env.pop('NODE_OPTIONS', None)
    log = output / 'review-command.log'
    try:
        with log.open('wb') as stream:
            process = subprocess.run(args, cwd=output, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                     timeout=90, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise Refused(f'official review did not finish; see {log}: {error}') from error
    if process.returncode != 0:
        raise Refused(f'official review exited {process.returncode}; see {log}')
    manifest = load(output / 'manifest.json')
    lint = output / 'lint.json'
    report = output / 'report.html'
    if (manifest.get('status') != 'done' or manifest.get('source') != 'file' or manifest.get('noAi') is not True
            or file_digest(output / 'design.enet') != source_hash or file_digest(netlist) != source_hash
            or report.stat().st_size == 0):
        raise Refused(f'official review result is incomplete or does not match its source; see {log}')
    findings = load(lint)
    counts = Counter(row['severity'] for row in findings['findings'])
    result = {'state': 'rule-report-ready', 'scope': 'supplementary-native-netlist',
              'ruleset_commit': REVIEW_REVISION, 'netlist_sha256': source_hash, 'lint_sha256': file_digest(lint),
              'severity_counts': {severity: counts[severity] for severity in ('P0', 'P1', 'P2', 'P3')},
              'coverage': findings['coverage'], 'findings': str(lint), 'report': str(report),
              'next_action': {'owner': 'assistant', 'task': '按位号、引脚和网络复核规则发现，结合方案、NC 与手册判断。'}}
    dump(output / 'eso-review.json', result)
    return result
