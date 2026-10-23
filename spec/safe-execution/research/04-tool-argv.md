# 04 — Tool-by-tool argv mapping

Source read: `tools/{filesystem,slurm,rcac,transfer,shell}.py`, `resources.py`, `executor/{ssh,delegate}.py` put/get.
`P(x)` = path marked for leading-`~`/`~/` resolution (UserPath). Uncertain items carry a **probe** (§ Probes).

## Rules applied

- **Option values: two elements, `--long`/`-s` then the value.** GNU `getopt_long` takes a *required* option argument
  from the next argv element whatever it begins with (`-p -x` sets `p="-x"`), and `--long VALUE` (separate element)
  is accepted for required-argument long options. Prefer the split form over `--long=value` because a `P(...)` value
  must stand as a whole element for the launcher to render `"$HOME"/'rest'`; bash does not tilde-expand
  `--output=~/x` (checked: `/bin/bash -c 'echo --output=~/x'` prints it literally). None of the options used below
  take an *optional* argument (`::`), where the split form would not bind.
- **Positionals after `--`** where the program takes positionals and accepts `--`. Programs with no positionals
  (`squeue`, `sacct`, `sinfo`) must not get `--` (squeue/sinfo reject leftover args — probe).
- **Fallback for programs without `--`:** a relative path beginning with `-` becomes `./-x` (meaning-preserving).
- **Launcher `cd` hazard (affects the hypothesis):** bash `cd -- DIR` still honours `CDPATH` and still maps `-` to
  `$OLDPWD` (both verified locally on /bin/bash 3.2). The launcher's `cd -- <cwd>` must neutralise both:
  prefix any relative cwd with `./` (or `CDPATH= cd`). Same for list_directory's `cd` below.

## Mapping table

| Tool · param | Type | Today (file:line) | Proposed argv |
|---|---|---|---|
| list_directory · path, show_hidden | str (P), bool | `ls {flags} {path}` filesystem.py:56–60; `echo $HOME` :87, `pwd` :95 **inside the per-entry loop** | one call, see §4 |
| read_file · path, max_size | str (P), int | `head -c {max_size} {path}` :143; `cat {path}` :145 | `['head','-c',str(n),'--',P(path)]` / `['cat','--',P(path)]` |
| read_file · encoding | str | unused | never reaches argv; see §5 |
| write_file · path, content, append, create_dirs | str (P), str→stdin, bool, bool | `mkdir -p "$(dirname {path})"` :178; heredoc :187 | §6 |
| sbatch · job_name/account/partition/qos/time/mem/gpus/constraint | str | `-J {v}` etc. slurm.py:126–146 | `'-J',v` `'-A',v` `'-p',v` `'-q',v` `'-t',v` `'--mem',v` `'--gpus',v` `'-C',v` |
| sbatch · nodes/ntasks/cpus_per_task | int | `-N {v}` :136–140 | `'-N',str(v)` … (keep today's truthiness) |
| sbatch · output, error | str (**P**) | `-o {v}` `-e {v}` :148–150 | `'-o',P(v)` `'-e',P(v)` (Slurm does no `~` expansion itself; today bash expanded the split word, so P keeps behaviour) |
| sbatch · extra_args | str → list[str] (R6) | appended raw :152 | elements spliced before `'--'` |
| sbatch · script_path / args (new) | str (P) / list[str] | appended raw :154 | `[...opts, *extra_args, '--', P(script_path), *args]` |
| sbatch · script_content | str→stdin | heredoc :114, `mkdir -p ~/jobs` :105, `chmod +x` :121 | §7 |
| squeue · user/account/partition/state | str | `-u {v}` … :212–218 | `'-u',v` `'-A',v` `'-p',v` `'-t',v` |
| squeue · job_ids / me | list[int] / bool | `-j 1,2` :220, `--me` :210 | `'-j','1,2'` / `'--me'` |
| scancel · job_ids | list[int] | space-joined :275 | `'--', *map(str, ids)` **last** (ints; `--` optional but harmless) |
| scancel · user/account/partition/state/name | str | :279–287 | `'-u',v` `'-A',v` `'-p',v` `'-t',v` `'-n',v` |
| sacct · user/account/name/state/starttime/endtime | str | :348–362 (`--name={v}` :356) | `'-u',v` `'-A',v` `'--name',v` `'-s',v` `'-S',v` `'-E',v`; `whoami` :346 → `['whoami']` |
| sacct · format | str | `-o {format}` :364 (unquoted) | `'-o',format` (spaces/`%` intact) |
| sinfo · partition/nodes/state/format | str | :410–416 (`-o "{format}"`, so `"`/`$` broke) | `'-p',v` `'-n',v` `'-t',v` `'-o',format` |
| squeue/scancel/sacct/sinfo · extra_args | str | appended raw :222/:289/:366/:418 | **GOAL conflict, §2** |
| scontrol_show_job · job_id | int | `scontrol show job {id}` :449 | `['scontrol','show','job',str(id)]` |
| scontrol_show_node · node | str | `cmd += f' {node}'` :479 | `['scontrol','show',f'node={node}']` or `['scontrol','--','show','node',node]` — probe |
| slist, sfeatures, myquota, showpartitions, findscratch | — | literal strings | `['slist']` etc. |
| jobinfo/jobcmd/jobenv/jobscript · job_id | int | `f'jobinfo {id}'` rcac.py:80/107/134/161 | `['jobinfo',str(id)]` (int: no `--` needed) |
| average_wait · partition, account | str | `-p {v}` `-A {v}` rcac.py:220–222 | `'-p',v` `'-A',v` **only if** the script is getopt/getopts — probe, §10 |
| upload_file · remote_path / local_path | str (P) / server path | `readlink -f {remote}` transfer.py:34 | `['readlink','-f','--',P(remote)]`; §8 |
| download_file · remote_path / local_path | str (P) / server path | put/get only | §8 |
| run_command · cwd | str (P) | executor `cd` | launcher `cd`, `./`-prefixed if relative |
| resource cluster_context | — | `find … 2>/dev/null \| sort` resources.py:48; `cat {filepath}` :64 | §9 |

Complete list of P params: list_directory.path, read_file.path, write_file.path, upload_file.remote_path,
download_file.remote_path, sbatch.script_path/output/error, run_command.cwd, plus internal `~/jobs` (sbatch).
No other path-valued params exist (`sinfo.nodes` is a hostlist, not a path).

## 1. Slurm `--` and scontrol

- **sbatch** parses with a `+`-prefixed optstring (stops at the first non-option = script; everything after goes to
  the script). GNU getopt honours `--` in both modes, so `sbatch [opts] -- SCRIPT ARGS…` binds a `-x` script path
  and leaves `args` untouched (args were already safe after the script). High confidence; probe 1 confirms without
  submitting.
- **scancel** takes job IDs as positionals; with permutation, `--` before them is fine. Ours are ints — moot.
- **squeue/sacct/sinfo**: no positionals; every user value is an option argument, which getopt never parses. No `--`.
- **scontrol**: global getopt_long (I believe *without* `+`, i.e. permuting), so today `scontrol show node -x` reads
  `-x` as a scontrol option. Two candidate forms: (a) `scontrol show node=VALUE` — `_show_it` splits `tag=value`
  on the first `=`; one element that never begins with `-`; (b) `scontrol -- show node VALUE`. Medium confidence on
  both — probe 2.

## 2. `extra_args` on squeue/scancel/sacct/sinfo — GOAL conflict (needs a clarification)

R2 says *every* string param except `run_command.command` is one literal argument **never parsed as an option**;
these four `extra_args: Optional[str]` exist *to be* options. R6 converts only `sbatch.extra_args`; the non-goals
forbid new params and renames and say nothing about type changes outside R6. No option satisfies R2 as written:

| Option | Behaviour | Consequence |
|---|---|---|
| (a) one literal argv element | `'--long'`, `'--format=%i %j'` work; `'-l -t PD'` becomes one bogus short-option cluster | No schema change; multi-option strings that work today break; still "parsed as an option", so R2's letter fails |
| (b) `shlex.split` | Today's multi-option usage kept; no shell expansion (`$`, `*` literal); unbalanced quotes → ValueError naming the param | No schema change; R2's "`My Data` is one argument" fails by design; a second, quote-aware parsing rule to document and test |
| (c) `list[str]` like sbatch | Each element one argv; string refused by schema | Clean and consistent with R6; a surface change GOAL hasn't sanctioned (update `tests/fixtures/served_surface.json`, landing notes) |
| (d) refuse leading `-` | Every option begins with `-` | Effectively deletes the param |

Every option needs R2 to say `extra_args` values are passed on to the program as options, deliberately. They are
still never shell-interpreted. Recommendation: (c) for all five, with the R2 exemption written in; (b) if surface
churn must stay at R6's two changes. Also refuse an `extra_args` element equal to `--`. In sbatch it would end
option parsing early, and our own `--` would then be taken as the script.

## 3. format, ints

`format` goes in as one element after `-o`; spaces, `%`, `,` and quotes stay intact. That fixes `sinfo`'s
`-o "{format}"` (:416), which breaks on `"` or `$`. `job_ids` are joined with `,` after `-j`, or given as separate
elements after `--` for scancel. Ints are `str(int)`. Pydantic already enforces the `int` type.

## 4. list_directory

**Bugs today:** `flags += 'a'` (:58) produces `--time-style=long-isoa`, which GNU ls rejects. So
`show_hidden=True` fails today. That comes from coreutils source, not a run; probe 5 confirms it. `echo $HOME` and
`pwd` run once per entry inside the loop (N+1 round trips). `--time-style` is GNU-only, so local mode on macOS is
already broken (pre-existing; out of scope unless the R2 test runs local mode on a macOS dev box).

Proposed single call (a constant script, with the user value as `$1`; a list, so R1 holds):
`['bash','-c','cd -- "$1" && printf "%s\\0" "$PWD" && exec ls -l --time-style=long-iso "${@:2}" .', 'list_directory', P'(path), *(['-a'] if show_hidden else [])]`.
P' adds the `./` prefix when the path is relative (defeats CDPATH and `cd -`). Split stdout on the first NUL to
get the absolute directory, which is logical like today's (no symlink resolution). Consequence: a symlink to a
directory is now listed through. Today `ls -l link` shows the link itself, so that is a small behaviour change;
use `ls -l -- P` plus the same `cd` for `$PWD` if parity matters.
Name parsing (`split(None,7)`, :74) still breaks on names with leading spaces or embedded newlines, symlinks
(`a -> b`), and device files (size `8, 1` makes `int()` raise). It is not R2's argument path, but the R2 fixture
tree should avoid such names, or the plan should adopt `--quoting-style`/`find -printf '…\0'`.

## 5. read_file

`['cat','--',P]`, or `['head','-c',str(max_size),'--',P]`. `max_size=0` reads the whole file today (`if max_size:`),
and a negative value means "all but the last N" to GNU head and is an error on BSD. Validate `max_size >= 1`, or
treat 0 as empty. `encoding` is accepted and ignored today (the executor decodes stdout). As a string param it never
reaches argv, so R2 holds vacuously. The non-goal says read_file gains no encoding, so leave it dead but documented.

## 6. write_file

Order: validate first, then mkdir, then write. (1) `encoding` typed `Literal['utf-8','base64']`: the schema
refuses other values with an error naming the param (R5). (2) Compute `data` before any executor call:
`content.encode('utf-8')`, where a lone surrogate raises → re-raise naming `content`; or
`base64.b64decode(content, validate=True)`, where `binascii.Error` → error naming `content`. `validate=True`
rejects newline-wrapped (MIME) base64; strip ASCII whitespace first or refuse it (planner's choice). (3) If
`create_dirs`: `['mkdir','-p','--',D]` with `D = posixpath.dirname(path)` re-wrapped as P (`~/a/b`→`~/a`,
`~/x`→`~`, `~foo/x`→`~foo`, stays literal); skip when D is `''`. Alternative: fold it into the write as
`mkdir -p -- "$(dirname -- "$1")"`, which gives one round trip and computes dirname after `$HOME` is resolved.
(4) `['bash','-c','cat >"$1"','write_file',P]` (`>>` when appending), with `stdin=data`. That is byte-exact,
`''` creates or truncates, and nothing touches the target before validation passes.

## 7. sbatch script_content

`['mkdir','-p','--',P('~/jobs')]`, then `['bash','-c','cat >"$1" && chmod +x -- "$1"','sbatch',P(spath)]` with
`stdin=script_content.encode()` (one call; R4 byte-exact). **Flag (job_name → path):** `job_name` stays literal
for `-J`, but `f'~/jobs/{name}_{ts}.sh'` (:110) lets `../x` write to `~/x_<ts>.sh` and lets `a/b` fail (no parent
directory). It is a separate use of the value. Sanitise only the filename component
(`re.sub(r'[^A-Za-z0-9._-]','_',name).lstrip('.-') or 'job'`). The docstring says only that the name is "used in"
the filename, so that is no surface change. Second-resolution timestamps can collide and overwrite an earlier
script; add microseconds or the PID. `extra_args` go before `--`, and `args` after the script.

## 8. upload/download

The remote side: `readlink -f -- P` is GNU, and macOS has `-f` since 12.3. Resolving `~` inside put/get is the
executor's job. In ssh mode, SFTP does not expand `~`: Fabric joins a relative remote onto the SFTP cwd, so
`~/x` becomes `$HOME/~/x`. Map `~/rest` to the relative `rest` (the SFTP cwd is the login home) and a bare `~`
to `.`. In delegate mode, use the mapped user's home from `pwd.getpwnam(user).pw_dir`, with no sudo round trip.
Today delegate `put` is `sudo -n cp local remote` with no `--` and no `-u` (delegate.py:172), and `get` is
`sudo … cat remote` with no `--` (:196). Both need `--`. Both should run as the mapped user
(research 06, A3). **local_path** is a path on the server's filesystem, opened by Python or by
delegate's `cp`, never a remote command line. Its `~` should mean the *server process* home
(`os.path.expanduser`). R3 says "the user the command runs as", which is ambiguous for local_path in delegate
mode; that is a clarification candidate.

## 9. resources.py

`['find','/etc/agents.d','-maxdepth','1','-name','*.md','-type','f','-print0']`. Sort in Python, and drop
`2>/dev/null` because stderr is captured separately. Change the guard to stdout-based. With no pipeline, find's
exit 1 (a missing directory or one unreadable file) now surfaces. A missing directory still yields `''`, but one
unreadable file should not drop the rest. Then `['cat','--',f]` per file. Python's `sorted()` is bytewise, while
`sort` follows the locale, so order may change for mixed-case names.

## 10. RCAC site scripts

`jobinfo`/`jobcmd`/`jobenv`/`jobscript` take an int, so they're safe whatever parses it. For `average_wait`,
safety depends on the implementation. `getopts` in sh or bash takes the next word as OPTARG even when it begins
with `-`, so it's safe. Python argparse **refuses** `-p -x` ("expected one argument") but accepts `-p-x` or
`--partition=-x`. If the scripts are shell functions from `/etc/profile.d`, a non-login `bash -c` won't see them,
which ties into the login-flag research.

## Probes (read-only, on a login node)

```
# 1 sbatch honours --; expect "Unable to open file", no submission
sbatch -- ./-probe-nonexistent.sh; echo rc=$?
sbatch --version
# 2 scontrol forms; expect "Node -probe not found" vs "invalid option"
scontrol show node -probe; scontrol -- show node -probe; scontrol show node=-probe; scontrol show node -- -probe
# 3 option values starting with '-' bind as values; positionals rejected
squeue --me -p -probe; sinfo -p -probe; sacct -X -n -S now -A -probe; squeue --me -- x; echo rc=$?
# 4 site scripts: kind, parser, login-vs-nonlogin visibility
for s in average_wait jobinfo jobcmd jobenv jobscript myquota findscratch showpartitions slist sfeatures; do bash -c "type -a $s"; bash -lc "type -a $s"; done
file "$(command -v average_wait)"; head -40 "$(command -v average_wait)"; average_wait --help; average_wait -h
head -20 "$(command -v jobinfo)"
# 5 GNU tools
ls --version | head -1; ls -l --time-style=long-isoa / >/dev/null; echo rc=$?; readlink --version | head -1
```
