# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The sdist hygiene check (`bin/sdist_hygiene.py`) names every piece of furniture and passes nothing else.

The script is the one definition the release recipe's gate and CI both run, so these tests load
it by path and call it in-process: no subprocess, so the unit guard holds. Tarballs are built in
`tmp_path` with the layout hatchling produces, one root directory named after the archive.
"""

from __future__ import annotations

import bz2
import gzip
import importlib.util
import io
import lzma
import random
import re
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


def tar_bytes(members: list[str], big: int = 0, fmt: int = tarfile.PAX_FORMAT) -> bytes:
    """An uncompressed tarball holding `members` (each a small file), then `big` random bytes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=fmt) as archive:
        for member in members:
            info = tarfile.TarInfo(member)
            if member.endswith('/'):
                info.type = tarfile.DIRTYPE
                archive.addfile(info)
                continue
            info.size = 2
            archive.addfile(info, io.BytesIO(b'x\n'))
        if big:
            data = random.Random(0).randbytes(big)
            info = tarfile.TarInfo(f'{ROOT}/src/rcac_mcp/big.bin')
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def linked(kind: bytes, name: str, target: str) -> bytes:
    """An uncompressed tarball of `CLEAN` plus one link member `name` pointing at `target`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for member in CLEAN:
            info = tarfile.TarInfo(member)
            info.size = 2
            archive.addfile(info, io.BytesIO(b'x\n'))
        info = tarfile.TarInfo(name)
        info.type, info.linkname = kind, target
        archive.addfile(info)
    return buffer.getvalue()


def header_at(data: bytes, name: str) -> int:
    """Offset of `name`'s own header block in an uncompressed tarball."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return archive.getmember(name).offset_data - 512


def end_at(data: bytes) -> int:
    """Offset of the end-of-archive marker in an uncompressed tarball."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        last = archive.getmembers()[-1]
    return last.offset_data + -(-last.size // 512) * 512


def flip(data: bytes, at: int) -> bytes:
    raw = bytearray(data)
    raw[at] ^= 0xFF
    return bytes(raw)


def unreadable_shapes() -> dict[str, bytes]:
    """Tarball bytes that cannot be read in full, each in its own way."""
    big = tar_bytes(CLEAN, big=200_000)
    big_gz = gz(big)
    hidden = tar_bytes([*CLEAN, f'{ROOT}/.security/hidden.md', f'{ROOT}/src/z.py'])
    bad_header = bytearray(hidden)
    at = header_at(hidden, f'{ROOT}/.security/hidden.md')
    bad_header[at + 148 : at + 156] = b'0000000\x00'
    clean = tar_bytes(CLEAN)
    after_end = bytearray(clean)
    after_end[end_at(clean) + 1024 : end_at(clean) + 1024] = tar_bytes([f'{ROOT}/.security/after.md'])[:1024]
    short = tar_bytes([*CLEAN, f'{ROOT}/src/last.py'])
    head = clean[: end_at(clean)]
    hidden_tail = tar_bytes([f'{ROOT}/.security/hidden.md'])
    # A furniture member a mebibyte of zeros past the end of the archive.
    far_gz = gz(clean + bytes(1 << 20) + tar_bytes([f'{ROOT}/.security/far.md'])[:1024] + bytes(1024))
    long_tail_gz = gz(clean + bytes(1 << 20))
    second_bz2 = bytearray(bz2.compress(hidden_tail))
    second_bz2[0] ^= 0xFF
    second_xz = bytearray(lzma.compress(hidden_tail))
    second_xz[20] ^= 0xFF
    first_gzip_member = gz(clean[: header_at(clean, CLEAN[2])])
    # The damaged header is the last non-zero block, so only a read that starts at it sees it.
    last = tar_bytes([*CLEAN, f'{ROOT}/.security/'])
    bad_last = bytearray(last)
    at = header_at(last, f'{ROOT}/.security')
    bad_last[at + 148 : at + 156] = b'0000000\x00'
    return {
        'truncated-gzip': big_gz[: len(big_gz) // 2],
        'not-a-tarball': b'not a tarball',
        'empty': b'',
        'deflate-damaged': flip(big_gz, len(big_gz) // 2),
        'gzip-checksum-damaged': flip(big_gz, len(big_gz) - 6),
        'gzip-length-damaged': flip(big_gz, len(big_gz) - 2),
        'header-damaged-mid-archive': gz(bytes(bad_header)),
        'member-data-cut-short': gz(short[: header_at(short, f'{ROOT}/src/last.py') + 512]),
        'member-after-end-of-archive': gz(bytes(after_end)),
        'last-header-damaged': gz(bytes(bad_last)),
        'garbage-after-gzip-stream': gz(clean) + b'garbage',
        'no-end-of-archive': gz(head),
        'uncompressed-cut-at-a-header': clean[: header_at(clean, CLEAN[2])],
        'second-gzip-member-missing': first_gzip_member,
        'member-after-long-zero-tail': far_gz,
        'checksum-damaged-after-long-zero-tail': flip(long_tail_gz, len(long_tail_gz) - 6),
        'second-bz2-stream-damaged': bz2.compress(clean) + bytes(second_bz2),
        'second-xz-stream-damaged': lzma.compress(clean) + bytes(second_xz),
        'garbage-after-xz-stream': lzma.compress(clean) + b'garbage',
        'truncated-xz': lzma.compress(big)[:-40],
        'truncated-bz2': bz2.compress(big)[:-40],
    }


UNREADABLE = unreadable_shapes()


def cannot_be_read(path: Path, line: str) -> bool:
    """True when `line` is the one formatted unreadable line for `path`, with a reason."""
    return re.fullmatch(re.escape(f'{path}: cannot be read (') + r'\S[^\n]*\)', line) is not None


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


@mark.unit
@mark.parametrize('shape', list(UNREADABLE))
def test_unreadable_sdist_is_named_and_fails(tmp_path: Path, capsys, shape: str) -> None:
    path = tmp_path / f'{ROOT}.tar.gz'
    path.write_bytes(UNREADABLE[shape])
    assert hygiene.main([str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert captured.out == ''
    lines = captured.err.splitlines()
    assert len(lines) == 2 and cannot_be_read(path, lines[0]), lines
    assert lines[1] == 'sdist hygiene: 1 problem(s); see above'


@mark.unit
def test_every_truncation_of_a_gzip_stream_is_named(tmp_path: Path) -> None:
    whole = gz(tar_bytes(CLEAN, big=200_000))
    cuts = sorted({*range(len(whole) - 64, len(whole)), *(len(whole) * k // 16 for k in range(1, 16))})
    path = tmp_path / f'{ROOT}.tar.gz'
    passed = []
    for cut in cuts:
        path.write_bytes(whole[:cut])
        found = hygiene.sdist_violations(tmp_path)
        if not (len(found) == 1 and cannot_be_read(path, found[0])):
            passed.append((cut, found))
    assert passed == []


@mark.unit
def test_a_directory_named_like_an_sdist_is_named(tmp_path: Path) -> None:
    path = tmp_path / f'{ROOT}.tar.gz'
    path.mkdir()
    found = hygiene.sdist_violations(tmp_path)
    assert len(found) == 1 and cannot_be_read(path, found[0])


@mark.unit
def test_any_error_while_reading_is_named(tmp_path: Path, monkeypatch) -> None:
    path = sdist(tmp_path, CLEAN)

    def broken(*args, **kwargs):
        raise RuntimeError('planted failure')

    monkeypatch.setattr(hygiene.tarfile, 'open', broken)
    assert hygiene.sdist_violations(tmp_path) == [f'{path}: cannot be read (RuntimeError: planted failure)']


@mark.unit
def test_an_unreadable_sdist_does_not_stop_the_others(tmp_path: Path, capsys) -> None:
    sdist(tmp_path, [m.replace(ROOT, 'aaa-0.1.0', 1) for m in CLEAN], name='aaa-0.1.0')
    broken = tmp_path / 'mmm-0.1.0.tar.gz'
    broken.write_bytes(UNREADABLE['truncated-gzip'])
    bad = sdist(tmp_path, ['zzz-0.1.0/PKG-INFO', 'zzz-0.1.0/.security/x'], name='zzz-0.1.0')
    assert hygiene.main([str(tmp_path)]) == 1
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 3 and cannot_be_read(broken, lines[0]), lines
    assert lines[1:] == [f'{bad}: .security/x', 'sdist hygiene: 2 problem(s); see above']


@mark.unit
@mark.parametrize('shape', ['gnu-format', 'uncompressed', 'two-gzip-members', 'gzip-zero-padding', 'two-xz-streams',
                           'two-bz2-streams'])
def test_readable_variants_are_judged_as_before(tmp_path: Path, capsys, shape: str) -> None:
    clean = tar_bytes(CLEAN)
    data = {
        'gnu-format': gz(tar_bytes([*CLEAN, f'{ROOT}/src/{"n" * 120}.py'], fmt=tarfile.GNU_FORMAT)),
        'uncompressed': clean,
        'two-gzip-members': gz(clean[:1024]) + gz(clean[1024:]),
        'gzip-zero-padding': gz(clean) + bytes(512),
        'two-xz-streams': lzma.compress(clean[:1024]) + lzma.compress(clean[1024:]),
        'two-bz2-streams': bz2.compress(clean[:1024]) + bz2.compress(clean[1024:]),
    }[shape]
    (tmp_path / f'{ROOT}.tar.gz').write_bytes(data)
    assert hygiene.main([str(tmp_path)]) == 0
    assert capsys.readouterr().out == f'sdist hygiene: {ROOT}.tar.gz clean\n'


@mark.unit
def test_furniture_in_another_compression_is_named(tmp_path: Path) -> None:
    path = tmp_path / f'{ROOT}.tar.gz'
    path.write_bytes(bz2.compress(tar_bytes([*CLEAN, f'{ROOT}/spec/GOAL.md'])))
    assert hygiene.sdist_violations(tmp_path) == [f'{path}: spec/GOAL.md']


@mark.unit
@mark.parametrize(
    ('shape', 'expected'),
    [
        ('directory', ['.security']),
        ('symlink', ['bin']),
        ('hardlink', ['spec/link.md']),
        ('duplicates', ['spec/b.md', '.security/a.md', 'spec/b.md']),
        ('non-ascii-gnu', ['spec/caf\u00e9.md']),
    ],
)
def test_every_kind_of_member_is_judged(tmp_path: Path, shape: str, expected: list[str]) -> None:
    data = {
        'directory': tar_bytes([*CLEAN, f'{ROOT}/.security/']),
        'symlink': linked(tarfile.SYMTYPE, f'{ROOT}/bin', 'src'),
        'hardlink': linked(tarfile.LNKTYPE, f'{ROOT}/spec/link.md', f'{ROOT}/PKG-INFO'),
        'duplicates': tar_bytes([*CLEAN, f'{ROOT}/spec/b.md', f'{ROOT}/.security/a.md', f'{ROOT}/spec/b.md']),
        'non-ascii-gnu': tar_bytes([*CLEAN, f'{ROOT}/spec/caf\u00e9.md'], fmt=tarfile.GNU_FORMAT),
    }[shape]
    path = tmp_path / f'{ROOT}.tar.gz'
    path.write_bytes(gz(data))
    assert hygiene.sdist_violations(tmp_path) == [f'{path}: {name}' for name in expected]
