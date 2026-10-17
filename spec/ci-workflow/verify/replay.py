"""Replay one workflow job's run steps on a snapshot of the tree, close to how the runner runs them.

    uv run --with pyyaml python spec/ci-workflow/verify/replay.py [--from worktree|HEAD]
        [--job gate|mutants] [--plant KIND] [--expect-red STEP --expect-log TEXT]

The snapshot is a depth-1 fetch of one commit into a fresh repository, as `actions/checkout`
makes: HEAD itself, or (the default) a commit synthesized from what `git add -A` would stage now,
through a private index, so the real index and HEAD are untouched. Steps read the snapshot's own
workflow file and run in order, each as `bash -eo pipefail -c` (the workflow's
`defaults.run.shell: bash`). The environment is an allowlist (PATH, HOME, TMPDIR, locale, user,
SHELL) plus what the workflow's setup gives a step: `CI=true`, `UV_PYTHON=3.14`, and the pinned
uv (`static.UV_VERSION`, installed into the scratch directory and first on PATH; its version is
asserted). Nothing else of the caller's leaks in: no `VIRTUAL_ENV`, `UV_*`, `PYTEST_ADDOPTS` or
`BASH_ENV`. After the first step (`uv sync --locked`) the replay asserts that `rcac_mcp` imports
from the snapshot, never from the caller's tree. The first failing step stops the job, as on
GitHub.

The audit step (`bin/audit_lock.py`) asks the advisory service for real, so a replay needs the
network, as the runner has. Offline it is red at that step (the script exits 2, "advisory source
unreachable"), never skipped.

What it cannot reproduce: the OS (macOS here, Ubuntu on the runner) and bash itself (macOS ships
bash 3.2; the runner has 5.x). A replay is local evidence that the steps, plants and guards behave;
the only Linux evidence is the green run on GitHub.

Without --expect-red the replay passes only when every step exits 0. With it, it passes only
when the first failing step is the one whose command contains STEP, every earlier step passed,
and that step's output contains TEXT: a red for the wrong reason is a failure of the replay.

Plants (applied to the snapshot only):
  test-red            a unit test that fails                        (red at `uv run pytest -q`)
  agents-furniture    an untracked .agents/planted.md, which no exclude rule covers
                                                                    (red at the hygiene step)
  security-furniture  .security/planted.md, with its pyproject exclude and .gitignore line
                      removed so the build ships it                 (red at the hygiene step)
  empty-cases         `CASES = []` appended to tests/mutants.py     (mutants job: red)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import static  # noqa: E402

KEEP = ('PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE', 'USER', 'LOGNAME', 'SHELL')


def git(*args: str, cwd: Path, env: dict | None = None) -> str:
    return subprocess.run(['git', *args], cwd=cwd, env=env, check=True, text=True,
                          capture_output=True).stdout.strip()


def snapshot_commit(root: Path, source: str, scratch: Path) -> str:
    if source == 'HEAD':
        return git('rev-parse', 'HEAD', cwd=root)
    index = scratch / 'index'
    shutil.copy(git('rev-parse', '--path-format=absolute', '--git-path', 'index', cwd=root), index)
    env = dict(os.environ, GIT_INDEX_FILE=str(index))
    git('add', '-A', cwd=root, env=env)
    tree = git('write-tree', cwd=root, env=env)
    return git('commit-tree', tree, '-p', 'HEAD', '-m', 'ci-workflow replay snapshot', cwd=root)


def snapshot(root: Path, source: str, scratch: Path) -> tuple[str, Path]:
    """Fetch one commit at depth 1 into scratch/repo and check it out detached."""
    commit = snapshot_commit(root, source, scratch)
    repo = scratch / 'repo'
    git('init', '-q', str(repo), cwd=scratch)
    git('fetch', '-q', '--depth', '1', '--upload-pack', 'git -c uploadpack.allowAnySHA1InWant=true upload-pack',
        f'file://{root}', commit, cwd=repo)
    git('checkout', '-q', '--detach', 'FETCH_HEAD', cwd=repo)
    return commit, repo


def runner_env(scratch: Path) -> dict[str, str]:
    """The step environment: an allowlist, the workflow's setup variables, and the pinned uv first on PATH."""
    tools = scratch / 'uv-pinned'
    subprocess.run(['uv', 'venv', '-q', '--python', '3.14', str(tools)], check=True)
    subprocess.run(['uv', 'pip', 'install', '-q', '--python', str(tools / 'bin/python'), f'uv=={static.UV_VERSION}'],
                   check=True)
    env = {k: v for k, v in os.environ.items() if k in KEEP}
    env.update(PATH=f'{tools / "bin"}{os.pathsep}{env.get("PATH", "")}', CI='true', UV_PYTHON='3.14')
    version = subprocess.run(['uv', '--version'], env=env, text=True, capture_output=True).stdout.split()
    if version[:2] != ['uv', static.UV_VERSION]:
        sys.exit(f'replay: steps would run {" ".join(version)}, not uv {static.UV_VERSION}')
    return env


def plant(repo: Path, kind: str) -> None:
    if kind == 'test-red':
        (repo / 'tests/test_planted_red.py').write_text(
            'from pytest import mark\n\n\n@mark.unit\ndef test_planted_red() -> None:\n'
            "    assert False, 'planted red'\n")
    elif kind == 'agents-furniture':
        (repo / '.agents').mkdir()
        (repo / '.agents/planted.md').write_text('planted\n')
    elif kind == 'security-furniture':
        for name, line in (('pyproject.toml', '    "/.security",\n'), ('.gitignore', '.security/\n')):
            text = (repo / name).read_text()
            if text.count(line) != 1:
                sys.exit(f'replay: plant security-furniture no longer applies to {name}')
            (repo / name).write_text(text.replace(line, ''))
        (repo / '.security').mkdir()
        (repo / '.security/planted.md').write_text('planted\n')
    elif kind == 'empty-cases':
        with (repo / 'tests/mutants.py').open('a') as handle:
            handle.write('\nCASES = []\n')
    else:
        sys.exit(f'replay: unknown plant {kind}')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--from', dest='source', choices=['worktree', 'HEAD'], default='worktree')
    parser.add_argument('--job', choices=['gate', 'mutants'], default='gate')
    parser.add_argument('--plant')
    parser.add_argument('--expect-red', metavar='STEP')
    parser.add_argument('--expect-log', metavar='TEXT')
    args = parser.parse_args()
    if bool(args.expect_red) != bool(args.expect_log):
        parser.error('--expect-red and --expect-log go together')
    root = Path(git('rev-parse', '--show-toplevel', cwd=Path.cwd()))
    with tempfile.TemporaryDirectory(prefix='ci-replay.') as tmp:
        scratch = Path(tmp)
        commit, repo = snapshot(root, args.source, scratch)
        steps = static.job_steps(static.load(repo), args.job)
        if args.plant:
            plant(repo, args.plant)
        env = runner_env(scratch)
        label = f'replay[{args.job} @ {commit[:10]} from {args.source}{" + " + args.plant if args.plant else ""}]'
        for number, step in enumerate(steps, 1):
            done = subprocess.run(['bash', '-eo', 'pipefail', '-c', step], cwd=repo, env=env, text=True,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            first = step.splitlines()[0]
            if done.returncode == 0:
                print(f'{label} step {number}/{len(steps)} ok: {first}')
                if number == 1:
                    origin = subprocess.run(['uv', 'run', 'python', '-c', 'import rcac_mcp; print(rcac_mcp.__file__)'],
                                            cwd=repo, env=env, text=True, capture_output=True).stdout.strip()
                    if not os.path.realpath(origin).startswith(os.path.realpath(repo) + os.sep):
                        print(f'{label}: rcac_mcp imports from {origin!r}, not the snapshot', file=sys.stderr)
                        return 1
                continue
            print(f'{label} step {number}/{len(steps)} FAILED (exit {done.returncode}): {first}')
            print(done.stdout[-4000:])
            if not args.expect_red:
                return 1
            if args.expect_red not in step:
                print(f'{label}: red at the wrong step; expected the one running {args.expect_red!r}', file=sys.stderr)
                return 1
            if args.expect_log not in done.stdout:
                print(f'{label}: the failing step does not name {args.expect_log!r}', file=sys.stderr)
                return 1
            print(f'{label}: red as expected at {first!r}, naming {args.expect_log!r}')
            return 0
        if args.expect_red:
            print(f'{label}: every step passed, but a red at {args.expect_red!r} was expected', file=sys.stderr)
            return 1
        print(f'{label}: all {len(steps)} steps green')
        return 0


if __name__ == '__main__':
    sys.exit(main())
