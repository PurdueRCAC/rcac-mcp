"""Planted dist directories for the sdist hygiene check, built offline and deterministically.

    python plants.py OUT_DIR        # writes OUT_DIR/<plant>/ for every plant in PLANTS

Each plant is a directory holding one or more `*.tar.gz` files, and `PLANTS` says what the check
must print for it: `drive.py` runs the real command over each directory and compares. Every byte
comes from the standard library and a fixed seed: no network, no clock, no build backend.

Expectations, per plant:
- `clean`: exit 0, stdout `sdist hygiene: <names> clean`, stderr empty.
- `lines`: exit 1; stderr is exactly these lines then the `N problem(s)` summary, N = len(lines).
  A line `<name>: !unreadable` stands for `<dir>/<name>: cannot be read (<one-line reason>)`;
  any other `<name>: <rest>` stands for `<dir>/<name>: <rest>`.
- `same_as_base`: the plant is readable, so the check's stdout, stderr and exit status must equal
  the base revision's, byte for byte.
"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import random
import sys
import tarfile
from pathlib import Path

ROOT = 'rcac_mcp-0.1.0'
NAME = f'{ROOT}.tar.gz'
CLEAN = [f'{ROOT}/PKG-INFO', f'{ROOT}/pyproject.toml', f'{ROOT}/src/rcac_mcp/__init__.py', f'{ROOT}/tests/test_cli.py']
BIG = f'{ROOT}/src/rcac_mcp/big.bin'
U = '!unreadable'


def tar_bytes(members: list[str], big: int = 0, fmt: int = tarfile.PAX_FORMAT) -> bytes:
    """An uncompressed tarball holding `members` (small files), plus `BIG` of `big` random bytes."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=fmt) as archive:
        for member in members:
            add(archive, member, b'x\n')
        if big:
            add(archive, BIG, random.Random(0).randbytes(big))
    return buffer.getvalue()


def linked(kind: bytes, name: str, target: str) -> bytes:
    """An uncompressed tarball of `CLEAN` plus one link member `name` pointing at `target`."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w', format=tarfile.PAX_FORMAT) as archive:
        for member in CLEAN:
            add(archive, member, b'x\n')
        info = tarfile.TarInfo(name)
        info.type, info.linkname, info.mtime = kind, target, 0
        archive.addfile(info)
    return buffer.getvalue()


def add(archive: tarfile.TarFile, name: str, data: bytes) -> None:
    """Add a file holding `data`, or a directory when `name` ends in a slash."""
    info = tarfile.TarInfo(name)
    if name.endswith('/'):
        info.type = tarfile.DIRTYPE
        info.mtime = 0
        archive.addfile(info)
        return
    info.size = len(data)
    info.mtime = 0
    archive.addfile(info, io.BytesIO(data))


def gz(data: bytes) -> bytes:
    return gzip.compress(data, mtime=0)


def header_offsets(data: bytes) -> dict[str, int]:
    """Map each member name to the offset of its own header block in an uncompressed tarball."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return {m.name: m.offset_data - 512 for m in archive.getmembers()}


def corrupt_header(data: bytes, name: str) -> bytes:
    """Break the checksum of `name`'s header block, leaving every other byte alone."""
    at = header_offsets(data)[name]
    raw = bytearray(data)
    raw[at + 148:at + 156] = b'0000000\x00'
    return bytes(raw)


def end_of_archive(data: bytes) -> int:
    """Offset of the first zero block after the last member."""
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        last = archive.getmembers()[-1]
    return last.offset_data + -(-last.size // 512) * 512


def flip(data: bytes, at: int) -> bytes:
    raw = bytearray(data)
    raw[at] ^= 0xFF
    return bytes(raw)


def build() -> dict[str, dict]:
    """Return {plant: {'files': {name: bytes}, expectation...}}."""
    clean = tar_bytes(CLEAN)
    big = tar_bytes(CLEAN, big=200_000)
    big_gz = gz(big)
    security = tar_bytes([*CLEAN, f'{ROOT}/.security/hidden.md', f'{ROOT}/src/z.py'])
    after_end = bytearray(clean)
    # A furniture member written past the end-of-archive marker, with zero padding after it.
    after_end[end_of_archive(clean) + 1024:end_of_archive(clean) + 1024] = tar_bytes([f'{ROOT}/.security/after.md'])[:1024]
    unpadded = clean[:end_of_archive(clean)]
    short = tar_bytes([*CLEAN, f'{ROOT}/src/last.py'])
    head = clean[:end_of_archive(clean)]
    hidden_tail = tar_bytes([f'{ROOT}/.security/hidden.md'])
    second_bz2, second_xz = bytearray(bz2.compress(hidden_tail)), bytearray(lzma.compress(hidden_tail))
    second_bz2[0] ^= 0xFF
    second_xz[20] ^= 0xFF
    long_tail = gz(clean + bytes(1 << 20))
    third = header_offsets(clean)[CLEAN[2]]
    # A damaged header that is the last non-zero block: only a read starting at it sees it.
    last = tar_bytes([*CLEAN, f'{ROOT}/.security/'])
    plants: dict[str, dict] = {
        # Readable: judged exactly as on the base revision.
        'clean': {'files': {NAME: gz(clean)}, 'clean': True, 'same_as_base': True},
        'clean-gnu': {'files': {NAME: gz(tar_bytes(CLEAN + [f'{ROOT}/src/' + 'n' * 120 + '.py'], fmt=tarfile.GNU_FORMAT))},
                      'clean': True, 'same_as_base': True},
        'clean-big': {'files': {NAME: big_gz}, 'clean': True, 'same_as_base': True},
        'clean-plain-tar': {'files': {NAME: clean}, 'clean': True, 'same_as_base': True},
        'clean-two-gzip-members': {'files': {NAME: gz(clean[:1024]) + gz(clean[1024:])}, 'clean': True, 'same_as_base': True},
        'clean-gzip-zero-padding': {'files': {NAME: gz(clean) + bytes(512)}, 'clean': True, 'same_as_base': True},
        'clean-two-xz-streams': {'files': {NAME: lzma.compress(clean[:1024]) + lzma.compress(clean[1024:])},
                                 'clean': True, 'same_as_base': True},
        'clean-two-bz2-streams': {'files': {NAME: bz2.compress(clean[:1024]) + bz2.compress(clean[1024:])},
                                  'clean': True, 'same_as_base': True},
        'clean-long-zero-tail': {'files': {NAME: long_tail}, 'clean': True, 'same_as_base': True},
        'furniture-directory': {'files': {NAME: gz(tar_bytes([*CLEAN, f'{ROOT}/.security/']))},
                                'lines': [f'{NAME}: .security'], 'same_as_base': True},
        'furniture-symlink': {'files': {NAME: gz(linked(tarfile.SYMTYPE, f'{ROOT}/bin', 'src'))},
                              'lines': [f'{NAME}: bin'], 'same_as_base': True},
        'furniture-hardlink': {'files': {NAME: gz(linked(tarfile.LNKTYPE, f'{ROOT}/spec/l.md', f'{ROOT}/PKG-INFO'))},
                               'lines': [f'{NAME}: spec/l.md'], 'same_as_base': True},
        'furniture-duplicates': {'files': {NAME: gz(tar_bytes([*CLEAN, f'{ROOT}/spec/b.md', f'{ROOT}/.security/a.md',
                                                                f'{ROOT}/spec/b.md']))},
                                 'lines': [f'{NAME}: spec/b.md', f'{NAME}: .security/a.md', f'{NAME}: spec/b.md'],
                                 'same_as_base': True},
        'furniture-non-ascii-gnu': {'files': {NAME: gz(tar_bytes([*CLEAN, f'{ROOT}/spec/caf\u00e9.md'], fmt=tarfile.GNU_FORMAT))},
                                    'lines': [f'{NAME}: spec/caf\u00e9.md'], 'same_as_base': True},
        'furniture': {'files': {NAME: gz(tar_bytes([*CLEAN, f'{ROOT}/.security/a.md', f'{ROOT}/bin/sandbox.sh']))},
                      'lines': [f'{NAME}: .security/a.md', f'{NAME}: bin/sandbox.sh'], 'same_as_base': True},
        'outside-root': {'files': {NAME: gz(tar_bytes([*CLEAN, '.security/top.md']))},
                         'lines': [f'{NAME}: .security/top.md (outside the sdist root {ROOT}/)'], 'same_as_base': True},
        'furniture-bz2': {'files': {NAME: bz2.compress(tar_bytes([*CLEAN, f'{ROOT}/spec/GOAL.md']))},
                          'lines': [f'{NAME}: spec/GOAL.md'], 'same_as_base': True},
        # Unreadable: one formatted line each, exit 1, never a traceback, never clean.
        'truncated-half': {'files': {NAME: big_gz[:len(big_gz) // 2]}, 'lines': [f'{NAME}: {U}']},
        'not-a-tarball': {'files': {NAME: b'not a tarball'}, 'lines': [f'{NAME}: {U}']},
        'empty': {'files': {NAME: b''}, 'lines': [f'{NAME}: {U}']},
        'deflate-flipped': {'files': {NAME: flip(big_gz, len(big_gz) // 2)}, 'lines': [f'{NAME}: {U}']},
        'crc-flipped': {'files': {NAME: flip(big_gz, len(big_gz) - 6)}, 'lines': [f'{NAME}: {U}']},
        'size-flipped': {'files': {NAME: flip(big_gz, len(big_gz) - 2)}, 'lines': [f'{NAME}: {U}']},
        'header-corrupt-mid': {'files': {NAME: gz(corrupt_header(security, f'{ROOT}/.security/hidden.md'))},
                               'lines': [f'{NAME}: {U}']},
        'member-data-short': {'files': {NAME: gz(short[:header_offsets(short)[f'{ROOT}/src/last.py'] + 512])},
                              'lines': [f'{NAME}: {U}']},
        'member-after-end': {'files': {NAME: gz(bytes(after_end))}, 'lines': [f'{NAME}: {U}']},
        'last-header-damaged': {'files': {NAME: gz(corrupt_header(last, f'{ROOT}/.security'))}, 'lines': [f'{NAME}: {U}']},
        'gzip-trailing-garbage': {'files': {NAME: gz(clean) + b'garbage'}, 'lines': [f'{NAME}: {U}']},
        'no-end-marker': {'files': {NAME: gz(unpadded)}, 'lines': [f'{NAME}: {U}']},
        'plain-tar-cut-at-header': {'files': {NAME: clean[:third]}, 'lines': [f'{NAME}: {U}']},
        'second-gzip-member-missing': {'files': {NAME: gz(clean[:third])}, 'lines': [f'{NAME}: {U}']},
        'member-after-long-zero-tail': {'files': {NAME: gz(clean + bytes(1 << 20) + tar_bytes([f'{ROOT}/.security/far.md'])[:1024]
                                                           + bytes(1024))}, 'lines': [f'{NAME}: {U}']},
        'crc-flipped-after-long-tail': {'files': {NAME: flip(long_tail, len(long_tail) - 6)}, 'lines': [f'{NAME}: {U}']},
        'bz2-second-stream-damaged': {'files': {NAME: bz2.compress(clean) + bytes(second_bz2)}, 'lines': [f'{NAME}: {U}']},
        'xz-second-stream-damaged': {'files': {NAME: lzma.compress(clean) + bytes(second_xz)}, 'lines': [f'{NAME}: {U}']},
        'xz-trailing-garbage': {'files': {NAME: lzma.compress(clean) + b'garbage'}, 'lines': [f'{NAME}: {U}']},
        'bz2-second-stream-damaged-unmarked': {'files': {NAME: bz2.compress(head) + bytes(second_bz2)},
                                               'lines': [f'{NAME}: {U}']},
        'xz-truncated': {'files': {NAME: lzma.compress(big)[:-40]}, 'lines': [f'{NAME}: {U}']},
        'bz2-truncated': {'files': {NAME: bz2.compress(big)[:-40]}, 'lines': [f'{NAME}: {U}']},
        # One unreadable tarball among others: every other one is still judged (sorted order).
        'mixed': {'files': {'aaa-0.1.0.tar.gz': gz(tar_bytes([m.replace(ROOT, 'aaa-0.1.0', 1) for m in CLEAN])),
                            'mmm-0.1.0.tar.gz': big_gz[:len(big_gz) // 2],
                            'zzz-0.1.0.tar.gz': gz(tar_bytes(['zzz-0.1.0/PKG-INFO', 'zzz-0.1.0/.security/x']))},
                  'lines': [f'mmm-0.1.0.tar.gz: {U}', 'zzz-0.1.0.tar.gz: .security/x']},
    }
    # Every cut in the last 64 bytes and at sixteen points through the stream: each is truncated.
    cuts = sorted({*range(len(big_gz) - 64, len(big_gz)), *(len(big_gz) * k // 16 for k in range(1, 16))})
    for cut in cuts:
        plants[f'cut-{cut}'] = {'files': {NAME: big_gz[:cut]}, 'lines': [f'{NAME}: {U}']}
    return plants


def write(out: Path) -> dict[str, dict]:
    plants = build()
    for plant, spec in plants.items():
        for name, data in spec['files'].items():
            (out / plant).mkdir(parents=True, exist_ok=True)
            (out / plant / name).write_bytes(data)
    # A directory named like an sdist cannot be opened as one.
    (out / 'directory' / NAME).mkdir(parents=True)
    plants['directory'] = {'files': {}, 'lines': [f'{NAME}: {U}']}
    return plants


if __name__ == '__main__':
    write(Path(sys.argv[1]))
