"""Read-only R7 re-measurement through the built launcher (safe-execution P7)."""
import shlex, sys, time
from rcac_mcp.executor import launch
from rcac_mcp.executor.ssh import SSHExecutor

OBS = open(sys.argv[2]).read().strip()
ARGV = ['/bin/sh', '-c', 'printf "argv: MODULEPATH=%s LOADED=%s\\n" "${MODULEPATH:+set}" "${LOADEDMODULES-unset}"']
ENV = 'env -i HOME="$HOME" USER="$USER" LOGNAME="$LOGNAME" PATH=/usr/bin:/bin SHELL={sh}'

host = sys.argv[1]
ex = SSHExecutor(host)
print(f'===== {host}')
t = time.monotonic(); r = ex.run(OBS); dt = time.monotonic() - t
print(f'--- SSHExecutor.run(script) [login shell from passwd]  exit={r.exit_code} {dt:.2f}s')
print(r.stdout, end=''); print('stderr:', repr(r.stderr))
r = ex.run(ARGV)
print(f'--- SSHExecutor.run(argv)  exit={r.exit_code}'); print(r.stdout, end=''); print('stderr:', repr(r.stderr))
r = ex.run(['/bin/sh', '-c', 'printf "%s\\n" "$SHELL"'])
print('--- login shell ($SHELL as sshd set it):', r.stdout.strip())
transport = ex._ensure_connected().client.get_transport()
prog, sentinel = launch.program(OBS)
wire = launch.ssh_command(prog)
print('--- built ssh exec string (head):', wire[:200])
for sh in ('/bin/bash', '/bin/zsh', '/bin/tcsh', '/bin/sh'):
    outer = f'{ENV.format(sh=sh)} {sh} -c {shlex.quote(wire)}'
    out, err, code = SSHExecutor._exec(transport, outer, None, 60)
    out, err, code = launch.separate(out, err, code, sentinel)
    print(f'--- sshd-like env -i, {sh} -c <built ssh exec string>  exit={code}')
    print(out.decode(), end=''); print('stderr:', repr(err.decode()))
# The local/delegate wire (bash_argv) run on the login node, as a local-mode server there would.
argv = launch.bash_argv(prog)
outer = f'{ENV.format(sh="/bin/bash")} ' + ' '.join(shlex.quote(a) for a in argv)
out, err, code = SSHExecutor._exec(transport, outer, None, 60)
out, err, code = launch.separate(out, err, code, sentinel)
print(f'--- sshd-like env -i, bash_argv(program) (the local/delegate wire)  exit={code}')
print(out.decode(), end=''); print('stderr:', repr(err.decode()))
# Startup cost: five trivial commands through the built launcher.
t = time.monotonic()
for _ in range(5):
    ex.run(['true'])
print(f'--- cost: 5 x run(["true"]) = {time.monotonic() - t:.2f}s')
ex.close()
