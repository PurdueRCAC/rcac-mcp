# RCAC MCP Server

Purdue RCAC MCP Server: Enables agentic development with HPC clusters and storage services.

## Quick Start (Desktop Clients)

For MCP-enabled desktop applications like Claude Desktop, Cursor, or Warp, add this server
to your MCP configuration with an SSH host pointing at your cluster:

```json
{
  "mcpServers": {
    "rcac": {
      "command": "uvx",
      "args": ["git+https://github.com/purduercac/rcac-mcp", "--ssh-host", "cluster.rcac.purdue.edu"]
    }
  }
}
```

This runs the server locally in `stdio` mode, executing commands on the cluster over SSH
using your existing `~/.ssh/config` and keys.

Alternatively, set `RCAC_SSH_HOST` in your environment and omit `--ssh-host` from the args.

If the server cannot connect, your client shows the reason as the error to its first request:
`cannot connect to <host>: <cause>` (unknown host or SSH alias, connection refused, authentication
failed, or timed out after 15 seconds per step unless your SSH config sets `ConnectTimeout`). Check
that `--ssh-host` names the SSH host or `~/.ssh/config` alias you use with `ssh`, not the MCP
server's name.

## Execution Modes

The server supports three execution modes:

- **`ssh`** (default for stdio) — Execute commands on a remote HPC cluster via SSH.
- **`local`** — Execute commands locally under bash. For development/testing only.
- **`delegate`** — Multi-user mode; run as a privileged process and delegate commands to
  authenticated users via `sudo`. Requires JWT or OIDC authentication.

In every mode a command runs in a login bash (`/bin/bash -l`), whatever the user's login shell, so
`module` and the site's tools behave the same for everyone. No startup file reads a command's stdin
or changes where it starts, and nothing the login initialisation prints is returned, with one
exception: over ssh, a background job started by the remote login shell's own rc file (zsh's
`.zshenv`, say) can write into a result while the command runs
([`issues/startup-background-jobs.md`](issues/startup-background-jobs.md)). A result holds all of
the command's output, however slowly it arrives; if something left running keeps the output open
after the command exits, the call returns once nothing has arrived for a second, and stderr ends
with a note saying so. Over ssh a command's stdin is sent only once it has started, so if the remote
login shell's rc file reads stdin, a call that sends stdin times out and never writes partial
content (`write_file` and `sbatch`'s job script both send stdin; without a timeout of their own,
they wait at most 30 s with nothing arriving for the command to start, then fail naming exit code
`-1001`). A command that exceeds its `timeout` returns what it printed so far with exit code
`-1001`.

See [SECURITY.md](SECURITY.md) for detailed architecture and configuration.

## Hosted HTTP Server

For a shared hosted instance with authentication:

### Basic HTTP Server

```bash
rcac-mcp -t http --ssh-host cluster.rcac.purdue.edu
```

### With JWT Authentication

```bash
export JWT_SECRET="your-secret-key-at-least-32-characters"
rcac-mcp -t http -a jwt -e delegate

# Generate a token for clients
rcac-mcp --generate-token --lifetime 86400
```

### Docker Compose with TLS

For production-like deployments with HTTPS:

```bash
# Install mkcert if needed
brew install mkcert
mkcert -install

# Generate certificates
mkdir -p certs
mkcert -cert-file certs/cert.pem -key-file certs/key.pem mcp.rcac.dev localhost 127.0.0.1

# Add to /etc/hosts
echo "127.0.0.1 mcp.rcac.dev" | sudo tee -a /etc/hosts

# Run
docker compose up
```

The server will be available at `https://mcp.rcac.dev:8443`.

## RCAC Documentation

This server does not search RCAC documentation. The separate `rcac-docs-mcp` service at
`https://docs.rcac.purdue.edu/mcp` does: add it to your MCP client alongside this server.

## Available Tools

### Shell & Filesystem

- `run_command(command, cwd, timeout)` — Execute a shell command on the remote system
- `list_directory(path, show_hidden)` — List contents of a directory
- `read_file(path, encoding, max_size)` — Read contents of a file
- `write_file(path, content, append, create_dirs, encoding)` — Write content to a file, byte for byte (`encoding="base64"` for binary)
- `upload_file(local_path, remote_path)` — Upload a file to the remote system
- `download_file(remote_path, local_path)` — Download a file from the remote system

### RCAC Cluster

- `myquota()` — Show storage usage and quota limits
- `storage_paths()` — Get resolved paths for home, scratch, and depot
- `jobinfo(job_id)` — Detailed job information
- `jobcmd(job_id)` — Command submitted for a job
- `jobenv(job_id)` — Environment variables for a job
- `jobscript(job_id)` — Full submission script for a job
- `showpartitions()` — Available partitions and their status
- `average_wait(partition, account)` — Queue wait time statistics

### Slurm

- `sbatch(...)` — Submit a batch job (from script path or inline content, written byte for byte); `args` are the job script's arguments, `extra_args` more sbatch options, each a list with one argument per element
- `squeue(...)` — View the job queue
- `scancel(...)` — Cancel jobs
- `sacct(...)` — Query job accounting history
- `sinfo(...)` — Cluster and partition status

The `extra_args` of `sbatch`, `squeue`, `scancel`, `sacct` and `sinfo` is a list of options, one argument per element (`extra_args=["--long"]`); a string is refused.
- `scontrol_show_job(job_id)` — Detailed Slurm job info
- `scontrol_show_node(node)` — Detailed node info
- `slist()` — Slurm accounts and usage (RCAC-specific)
- `sfeatures()` — Node hardware features and constraints (RCAC-specific)

## Development

```bash
uv sync
rcac-mcp -e local
```

Run the test suite:

```bash
pytest
```

## License

MIT
