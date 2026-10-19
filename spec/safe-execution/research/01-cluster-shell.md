# 01 — What a bash-invoked command sees on a real login node (R7)

Measured 2026-10-17, read-only, within the dogfood class: a Gautschi login node (Rocky 9.8, bash
5.1.8) and a Negishi login node (Rocky 8.10, bash 4.4.20), over SSH as the operator's account
(`<user>`), whose login shell is `/bin/zsh`. Gilbreth refused the connection at host-key verification and was not retried (it
would have meant writing `known_hosts`). No writes, no jobs, nothing created under home. Raw
commands and outputs are in [`01-cluster-shell/`](01-cluster-shell/); every probe prints the same
observation line ([`obs.sh`](01-cluster-shell/obs.sh)): `login_shell`, `BASH_ENV`, `type -t
module`, `LMOD_CMD`, whether `MODULEPATH` is set, `LOADEDMODULES`, `[ a == a ]`, and
`module -t list`.

The raw outputs are **redacted excerpts** (2026-10-18): Lmod's install prefix reads `<lmod>/` and the
site's scripts directory `<site-scripts>/`; sections that copied a site script's own text, a system
file's text, or the account's own startup files are cut to their heading, and what they showed is
stated below. One output (probe F, the head and the `sacct` call of the site's `average_wait`) is
dropped for the same reason; its finding is under Tool-facing facts.

## Verdict

**Run the pinned interpreter as a login shell: `/bin/bash -l -c PROGRAM`.** A login bash gives the
same module state whatever shell launched it; a non-login bash gives a different, mostly broken,
state for each launcher.

| Launcher (outer shell) → pinned bash | non-login `bash -c` | login `bash -l -c` |
|---|---|---|
| real `zsh` login (sshd → `zsh -c`) | `module` defined (via `BASH_ENV`), `MODULEPATH` **empty**, "No modules loaded" | full: `module` function, `MODULEPATH` set, site defaults loaded (`gcc`, `openmpi`, `xalt`, `modtree/cpu`, `rcac`) |
| sshd-like `env -i` → `bash -c` (a bash user) | **no `module`** ("command not found") | identical to the row above |
| sshd-like `env -i` → `zsh -c` | **no `module`** | identical |
| sshd-like `env -i` → `tcsh -c` | no `module` function, but `MODULEPATH`/`LOADEDMODULES` inherited | identical |
| sshd-like `env -i` → `sh -c` | **no `module`** | identical |

`[ a == a ]` succeeds in every bash row and fails in zsh (`zsh:6: = not found`, which aborts the
rest of the command list: probe A's last line). The emulated `env -i … zsh -c` row agrees with the
real zsh login on the login column, which is the control for the emulation
([`probeB.out`](01-cluster-shell/probeB.out)). The only difference left between launchers under
`-l` is `BASH_ENV`, which a zsh launch exports (from `/etc/profile.d/modules.sh`) and the others do
not; a login bash re-initialises Lmod from `/etc/profile.d` either way, so it does not change
`module` behaviour.

## How `module` initialises

- `/etc/profile.d/00-modulepath.sh`, `modules.sh`, `z01_default_module.sh` (both clusters) set
  `MODULEPATH`, define `module`, export `BASH_ENV` to Lmod's `init/bash`
  (under a different Lmod prefix on each cluster), and load the site defaults. Only a login shell
  (or `/etc/bashrc`'s non-login branch, reached only through a user's own `~/.bashrc`) sources them
  ([`probeC.out`](01-cluster-shell/probeC.out); the `/etc/bashrc` lines themselves are redacted).
- `BASH_ENV` alone defines the `module` function in a non-login bash, but without `MODULEPATH` it
  can load nothing: it is the "module exists but nothing works" state today's zsh users get.
- The site's job tools depend on it: `jobcmd`, `jobenv` and `jobscript` live in a site scripts
  directory (`<site-scripts>/`), which the `rcac` default module puts on `PATH`. Under today's `zsh -c`
  they are **not found** (only `jobinfo`, in `/usr/local/bin`, is); under `bash -l` all four
  resolve ([`probeE.out`](01-cluster-shell/probeE.out)). So the pinned login bash also repairs three
  tools for zsh users.

## Costs and noise

- A login bash prints nothing for this account: `bash -l -c true` writes 0 bytes to stdout and 0 to
  stderr on both clusters. A user's own `~/.bash_profile`/`~/.profile` could print; that is the
  user's configuration and is recorded as a risk, not designed around.
- Startup: `bash -c true` ×5 = 0.01–0.07 s; `bash -l -c true` ×5 = 1.25–1.47 s, so **≈0.25–0.3 s
  per command** (Lmod's default-module load). Tools should make one executor call where they make
  several today (`list_directory` re-runs `echo $HOME`/`pwd` per entry).

## The SSH bootstrap survives every permitted login shell

`/etc/shells` on both clusters permits sh, bash, zsh, ksh, csh and tcsh, so the remote command
string must parse identically under all of them. The wire tested in probe B:

```
exec /bin/bash -c 'exec /bin/bash -l -c "$(printf %s "$1" | base64 -d)" cluster-mcp' cluster-mcp <BASE64(PROGRAM)>
```

contains no `!`, no backslash and no newline, and every caller value travels inside the base64
word. It ran the observation program correctly under real zsh and under `env -i` launches of bash,
zsh, tcsh and sh on both clusters. `base64` is GNU coreutils 8.30/8.32 there.

## Tool-facing facts (probes D–G)

- Slurm 26.05.4. `scontrol show node -x` → `invalid option -- 'x'` (getopt permutes positionals);
  `scontrol show node -- -x` → `Node -x not found` (literal). `squeue -u -x` and `--user=-x` both
  take `-x` as the value (`Invalid user: -x`). `squeue` takes no positional operands
  (`squeue --me -- extra` → `Unrecognized option: extra`).
- `average_wait` forwards every argument but its own flags to `sacct` verbatim: its option loop
  keeps only its own `--raw`/`--raw-wait`, `-v`/`--verbose`, `--quiet` and `-h`/`--help` and
  accumulates everything else for one `sacct` call (probes D–F; the script's text is redacted). The tool
  passes `-p <partition>`, which `sacct` reads as `--parsable`; `sacct`'s partition option is
  `-r`/`--partition`. Pre-existing; an argv form using `--partition=<value>` repairs it.
- `jobinfo` is a Python script taking exactly one argument (probe D; its text is redacted).

## Built-launcher re-measurement (2026-10-17)

The build changed how bash starts after the measurement above: a `bash --posix` stage that parks
the caller's streams on descriptors 57–59, `/dev/null` for all three streams while the startup
files run, a prologue that resets aliases, functions, options and traps, and a `run_command` script
run in a fresh non-login bash that inherits the login environment and its exported functions. So
the measurement was re-run, read-only, through the code as built (`fix/safe-execution` at P6,
`a0756a8`): Gautschi and Negishi, over SSH as `<user>` (login shell `/bin/zsh`), within the
dogfood class. No writes, no jobs, nothing created under home; Gilbreth and Scholar were not
probed. The driver is [`01-cluster-shell/built.py`](01-cluster-shell/built.py) and the raw output
[`01-cluster-shell/built.out`](01-cluster-shell/built.out); the observation program is the same
[`obs.sh`](01-cluster-shell/obs.sh), sent as a `run_command` script.

Launchers, per cluster:

1. `SSHExecutor.run(<obs.sh>)`: the real path, sshd running the built exec string under the
   account's zsh login shell; plus `SSHExecutor.run(argv)` with an argv that prints `MODULEPATH`
   and `LOADEDMODULES`.
2. The built exec string (`launch.ssh_command(launch.program(<obs.sh>))`, which begins
   `exec /bin/bash --posix -c 'p=$(printf %s "$1" | base64 -d && printf .) && exec 57<&0 58>&1
   59>&2 </dev/null >/dev/null 2>&1 && exec /bin/bash -l -c -- "${p%.}"' cluster-mcp <BASE64>`)
   under `env -i HOME="$HOME" USER="$USER" LOGNAME="$LOGNAME" PATH=/usr/bin:/bin SHELL=<sh>`
   with `<sh> -c` for `/bin/bash`, `/bin/zsh`, `/bin/tcsh` and `/bin/sh`, as sshd would launch it
   for a user with that login shell.
3. The `local`/`delegate` wire, `launch.bash_argv(program)`, under the same `env -i` (what a
   `local`-mode server on a login node would run).

Every result went through `launch.separate`, as the backends do. Each launcher printed the **same
observation line** on its cluster (one distinct line out of six on each), exit 0, empty stderr:

| Cluster | `module` | `LMOD_CMD` | `MODULEPATH` | `module -t list` | `[ a == a ]` |
|---|---|---|---|---|---|
| Gautschi | function | `<lmod>/libexec/lmod` | set | `glibc` … `gcc/14.1.0`, `openmpi/5.0.5`, `xalt/3.1.4`, `modtree/cpu`, `rcac` | ok |
| Negishi | function | `<lmod>/libexec/lmod` | set | `gmp` … `gcc/12.2.0`, `openmpi/4.1.4`, `xalt/3.0.2`, `modtree/cpu`, `rcac` | ok |

The loaded set on each cluster is the one the login column above recorded. The argv command saw
`MODULEPATH` set and the same `LOADEDMODULES`.

Two fields differ from the earlier table, both by design: `login=no` and `BASH_ENV=` (empty) in
every row, because the observation program is a `run_command` script, which runs in the fresh
non-login bash the launcher starts with `BASH_ENV=` cleared; the login bash before it initialised
Lmod, and `module` reaches the script as an exported function. The one difference the earlier
measurement left between launchers (zsh exporting `BASH_ENV`) no longer reaches a command.

Cost: five `run(['true'])` calls took 1.59 s on Gautschi and 1.49 s on Negishi, ≈0.3 s each, as
estimated above.

**Verdict: module state is identical across launchers on both clusters** — the account's real zsh
login, sshd-like bash, zsh, tcsh and sh launches of the built exec string, and the local/delegate
wire. R7's `module` half holds for the code as built.
