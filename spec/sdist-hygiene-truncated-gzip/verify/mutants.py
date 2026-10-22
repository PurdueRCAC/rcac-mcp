"""Show that tests/test_sdist_hygiene.py goes red when the unreadable-sdist handling is broken.

    uv run python spec/sdist-hygiene-truncated-gzip/verify/mutants.py

Run from the repository root. Each mutant is one exact edit to bin/sdist_hygiene.py in a fresh
snapshot (pyproject.toml, the script, tests/conftest.py and the test module, copied from the
working tree), after which the test module runs there with a JUnit report. A mutant is killed only
when every named test id fails with a `<failure>` (an `<error>` is an import or syntax slip, not a
kill). The unmutated snapshot must first pass every test, and every named id must exist in it.
An `old` string that does not occur exactly once is red: the build moved the code, and this
table moves with it.

Footprint: one pytest child at a time; under 200 MB.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

SCRIPT = 'bin/sdist_hygiene.py'
MODULE = 'tests/test_sdist_hygiene.py'
COPIED = ['pyproject.toml', SCRIPT, 'tests/conftest.py', MODULE]
T = 'test_unreadable_sdist_is_named_and_fails'

# (name, old, new, test ids that must fail)
MUTANTS = [
    ('narrow-except', 'except Exception as error:', 'except (OSError, tarfile.TarError) as error:',
     [f'{T}[truncated-gzip]', 'test_any_error_while_reading_is_named']),
    ('marker-only', 'while block := stream.read(tarfile.RECORDSIZE):', 'while block := stream.read(0 if tail else tarfile.BLOCKSIZE):',
     [f'{T}[gzip-checksum-damaged]', f'{T}[member-after-end-of-archive]', f'{T}[garbage-after-gzip-stream]',
      f'{T}[member-after-long-zero-tail]']),
    ('nonzero-tail-allowed', 'if block.count(0) != len(block):', 'if False:',
     [f'{T}[header-damaged-mid-archive]', f'{T}[member-after-end-of-archive]', f'{T}[last-header-damaged]']),
    ('tail-read-from-current', 'stream.seek(archive.offset)', 'stream.tell()',
     [f'{T}[last-header-damaged]']),
    ('multi-line-reason', "reason = ' '.join(f'{type(error).__name__}: {error}'.split())",
     "reason = f'{type(error).__name__}: {error}'", [f'{T}[not-a-tarball]', f'{T}[empty]']),
    ('stop-at-unreadable', "found.append(f'{tarball}: cannot be read ({reason})')\n            continue",
     "found.append(f'{tarball}: cannot be read ({reason})')\n            break",
     ['test_an_unreadable_sdist_does_not_stop_the_others']),
    ('files-only', 'names = archive.getnames()', 'names = [m.name for m in archive.getmembers() if m.isfile()]',
     ['test_every_kind_of_member_is_judged[directory-expected0]', 'test_every_kind_of_member_is_judged[symlink-expected1]']),
    ('dedupe-sort', '    return names\n', '    return sorted(set(names))\n',
     ['test_every_kind_of_member_is_judged[duplicates-expected3]']),
    ('tail-capped', 'while block := stream.read(tarfile.RECORDSIZE):',
     'while (block := stream.read(tarfile.RECORDSIZE)) and stream.tell() <= archive.offset + 64 * tarfile.RECORDSIZE:',
     [f'{T}[member-after-long-zero-tail]', f'{T}[checksum-damaged-after-long-zero-tail]']),
    ('end-marker-optional', 'if tail < tarfile.BLOCKSIZE:', 'if False:',
     [f'{T}[no-end-of-archive]', f'{T}[uncompressed-cut-at-a-header]', f'{T}[second-gzip-member-missing]']),
    ('streams-trusted', '    if strict:\n', '    if False:\n',
     [f'{T}[second-bz2-stream-damaged]', f'{T}[second-xz-stream-damaged]', f'{T}[garbage-after-xz-stream]']),
    ('unreadable-dropped', "found.append(f'{tarball}: cannot be read ({reason})')", 'pass',
     [f'{T}[truncated-gzip]', f'{T}[truncated-xz]', 'test_every_truncation_of_a_gzip_stream_is_named']),
]


def outcomes(snapshot: Path) -> dict[str, str]:
    report = snapshot / 'junit.xml'
    report.unlink(missing_ok=True)
    root = Path.cwd()
    subprocess.run(['uv', 'run', '-q', '--project', str(root), 'pytest', '-q', '-p', 'no:cacheprovider', MODULE,
                    f'--junitxml={report}'], cwd=snapshot, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                   timeout=600)
    if not report.is_file():
        return {}
    result = {}
    for case in ET.parse(report).iter('testcase'):
        tags = {child.tag for child in case}
        result[case.get('name')] = ('failure' if 'failure' in tags else 'error' if 'error' in tags
                                    else 'skipped' if 'skipped' in tags else 'passed')
    return result


def main() -> int:
    red = 0
    with tempfile.TemporaryDirectory(prefix='sdist-mutants-') as scratch:
        snapshot = Path(scratch)
        for rel in COPIED:
            (snapshot / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(rel, snapshot / rel)
        pristine = (snapshot / SCRIPT).read_text()
        base = outcomes(snapshot)
        if not base or any(v != 'passed' for v in base.values()):
            print(f'mutants: the unmutated module is not green: {base or "no report"}', file=sys.stderr)
            return 1
        for name, old, new, targets in MUTANTS:
            missing = [t for t in targets if t not in base]
            if missing:
                print(f'mutants: {name}: no test named {missing}', file=sys.stderr)
                red += 1
                continue
            if pristine.count(old) != 1:
                print(f'mutants: {name}: anchor occurs {pristine.count(old)} times, not once', file=sys.stderr)
                red += 1
                continue
            (snapshot / SCRIPT).write_text(pristine.replace(old, new))
            got = outcomes(snapshot)
            (snapshot / SCRIPT).write_text(pristine)
            off = {t: got.get(t, 'absent') for t in targets if got.get(t) != 'failure'}
            if off:
                print(f'survived {name}: {off}', file=sys.stderr)
                red += 1
            else:
                print(f'killed {name}')
    if red:
        print(f'mutants: {red} of {len(MUTANTS)} red', file=sys.stderr)
        return 1
    print(f'mutants: {len(MUTANTS)} of {len(MUTANTS)} killed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
