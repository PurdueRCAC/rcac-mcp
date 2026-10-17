# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The sdist hygiene check (`bin/sdist_hygiene.py`) names every piece of furniture and passes nothing else.

The script is the one definition the release recipe's gate and CI both run, so these tests load
it by path and call it in-process: no subprocess, so the unit guard holds. Tarballs are built in
`tmp_path` with the layout hatchling produces, one root directory named after the archive.
"""

from __future__ import annotations

import importlib.util
import io
import tarfile
from pathlib import Path

from pytest import mark

SCRIPT = Path(__file__).resolve().parents[1] / 'bin' / 'sdist_hygiene.py'
_spec = importlib.util.spec_from_file_location('sdist_hygiene', SCRIPT)
assert _spec and _spec.loader
hygiene = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hygiene)

ROOT = 'rcac_mcp-0.1.0'
# Written out, not read from the script: a test parametrised over the script's own tuple loses
# the case for any entry the script drops, and passes.
RECORDED = ('.security', 'spec', 'issues', 'tests/fixtures', 'bin', '.agents')
CLEAN = [f'{ROOT}/PKG-INFO', f'{ROOT}/pyproject.toml', f'{ROOT}/src/rcac_mcp/__init__.py', f'{ROOT}/tests/test_cli.py']


def sdist(dist: Path, members: list[str], name: str = ROOT) -> Path:
    """Write a gzipped tarball holding `members` (each a small file) into `dist`."""
    dist.mkdir(parents=True, exist_ok=True)
    path = dist / f'{name}.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        for member in members:
            data = b'x\n'
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return path


@mark.unit
def test_clean_sdist_passes(tmp_path: Path) -> None:
    sdist(tmp_path, CLEAN)
    assert hygiene.sdist_violations(tmp_path) == []


@mark.unit
@mark.parametrize('entry', RECORDED)
def test_each_furniture_entry_is_named(tmp_path: Path, entry: str) -> None:
    path = sdist(tmp_path, [*CLEAN, f'{ROOT}/{entry}/planted.md'])
    assert hygiene.sdist_violations(tmp_path) == [f'{path}: {entry}/planted.md']


@mark.unit
def test_lookalikes_pass(tmp_path: Path) -> None:
    lookalikes = ['binary.txt', 'specs/x', 'issues.md', 'tests/fixturesX/a', 'tests/test_bin.py', '.securityx']
    sdist(tmp_path, [*CLEAN, *(f'{ROOT}/{name}' for name in lookalikes)])
    assert hygiene.sdist_violations(tmp_path) == []


@mark.unit
def test_member_outside_root_is_named(tmp_path: Path) -> None:
    path = sdist(tmp_path, [*CLEAN, '.security/top.md'])
    found = hygiene.sdist_violations(tmp_path)
    assert len(found) == 1 and found[0].startswith(f'{path}: .security/top.md') and 'outside the sdist root' in found[0]


@mark.unit
def test_dot_slash_prefix_is_read(tmp_path: Path) -> None:
    path = sdist(tmp_path, [*CLEAN, f'./{ROOT}/spec/GOAL.md'])
    assert hygiene.sdist_violations(tmp_path) == [f'{path}: spec/GOAL.md']


@mark.unit
def test_no_sdist_fails(tmp_path: Path) -> None:
    (tmp_path / 'rcac_mcp-0.1.0-py3-none-any.whl').write_bytes(b'')
    found = hygiene.sdist_violations(tmp_path)
    assert len(found) == 1 and 'no sdist' in found[0]


@mark.unit
def test_unreadable_tarball_fails(tmp_path: Path) -> None:
    (tmp_path / f'{ROOT}.tar.gz').write_bytes(b'not a tarball')
    found = hygiene.sdist_violations(tmp_path)
    assert len(found) == 1 and 'cannot be read' in found[0]


@mark.unit
def test_every_tarball_is_checked(tmp_path: Path) -> None:
    sdist(tmp_path, [m.replace(ROOT, 'aaa-0.1.0', 1) for m in CLEAN], name='aaa-0.1.0')
    bad = sdist(tmp_path, [f'zzz-0.1.0/{name}' for name in ('PKG-INFO', '.security/x')], name='zzz-0.1.0')
    assert hygiene.sdist_violations(tmp_path) == [f'{bad}: .security/x']


@mark.unit
def test_main_names_each_path_and_fails(tmp_path: Path, capsys) -> None:
    path = sdist(tmp_path, [*CLEAN, f'{ROOT}/.security/a.md', f'{ROOT}/bin/sandbox.sh'])
    assert hygiene.main([str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert f'{path}: .security/a.md' in err and f'{path}: bin/sandbox.sh' in err


@mark.unit
def test_main_fails_on_a_single_offender(tmp_path: Path, capsys) -> None:
    path = sdist(tmp_path, [*CLEAN, f'{ROOT}/issues/one.md'])
    assert hygiene.main([str(tmp_path)]) == 1
    assert f'{path}: issues/one.md' in capsys.readouterr().err


@mark.unit
def test_main_passes_a_clean_sdist(tmp_path: Path, capsys) -> None:
    sdist(tmp_path, CLEAN)
    assert hygiene.main([str(tmp_path)]) == 0
    assert 'clean' in capsys.readouterr().out


@mark.unit
def test_parent_reference_is_named(tmp_path: Path) -> None:
    path = sdist(tmp_path, [*CLEAN, f'{ROOT}/../escape.md'])
    found = hygiene.sdist_violations(tmp_path)
    assert len(found) == 1 and found[0].startswith(f'{path}: {ROOT}/../escape.md') and 'outside the sdist root' in found[0]


@mark.unit
def test_root_is_the_tarball_name(tmp_path: Path) -> None:
    # The root is read from the archive's name, never from its first member, so an archive laid
    # out under some other directory has every member reported.
    sdist(tmp_path, ['other-9.9/PKG-INFO', 'other-9.9/src/x.py'], name=ROOT)
    found = hygiene.sdist_violations(tmp_path)
    assert len(found) == 2 and all('outside the sdist root' in line for line in found)


@mark.unit
def test_furniture_matches_the_recorded_set() -> None:
    assert hygiene.FURNITURE == RECORDED
