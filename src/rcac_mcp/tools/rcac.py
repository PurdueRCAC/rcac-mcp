# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""RCAC-specific HPC cluster tools.

These tools wrap custom RCAC commands that are not part of standard Slurm.
They provide information about storage quotas, job details, partitions,
and queue statistics specific to Purdue's Research Computing clusters.
"""

# Type annotations
from __future__ import annotations
from typing import Optional, List
from dataclasses import dataclass

# Internal libs
from rcac_mcp.tools import mcp_tool
from rcac_mcp.context import get_executor


def _validate_job_id(job_id: int) -> None:
    """Validate that job_id is a positive integer."""
    if not isinstance(job_id, int) or job_id <= 0:
        raise ValueError(f'job_id must be a positive integer, got: {job_id}')


@mcp_tool
def myquota() -> str:
    """
    Show storage spaces, usage, and quotas for the current user.

    Displays all storage locations the user has access to (home, scratch,
    depot, etc.) along with current usage and quota limits. This is the
    authoritative way to discover what storage a user can access.

    The output shows:
    - Type: 'home', 'scratch', or 'depot'
    - Location: username (for home/scratch) or group name (for depot)

    Actual paths are:
    - home: /home/<location>
    - scratch: /scratch/<cluster>/<location> (or use $CLUSTER_SCRATCH)
    - depot: /depot/<location>

    Returns:
        Storage quota information showing type, location, size, limit,
        usage percentage, file counts, and file limits.

    Examples:
        myquota()
    """
    executor = get_executor()
    result = executor.run(['myquota'])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get quota info: {result.stderr}')

    return result.stdout


@mcp_tool
def jobinfo(job_id: int) -> str:
    """
    Get detailed information about a Slurm job.

    Shows job parameters including name, user, account, partition, nodes,
    cores, GPUs, state, exit code, timestamps, and resource usage.

    Args:
        job_id: The Slurm job ID to query.

    Returns:
        Detailed job information in key-value format.

    Examples:
        jobinfo(19804935)
    """
    _validate_job_id(job_id)
    executor = get_executor()
    result = executor.run(['jobinfo', str(job_id)])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get job info: {result.stderr}')

    return result.stdout


@mcp_tool
def jobcmd(job_id: int) -> str:
    """
    Get the command that was submitted for a Slurm job.

    Shows the actual command line or script invocation used when
    the job was submitted.

    Args:
        job_id: The Slurm job ID to query.

    Returns:
        The command that was submitted for the job.

    Examples:
        jobcmd(19804935)
    """
    _validate_job_id(job_id)
    executor = get_executor()
    result = executor.run(['jobcmd', str(job_id)])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get job command: {result.stderr}')

    return result.stdout


@mcp_tool
def jobenv(job_id: int) -> str:
    """
    Get the environment variables for a Slurm job.

    Shows all environment variables that were set when the job
    was submitted or that Slurm set for the job.

    Args:
        job_id: The Slurm job ID to query.

    Returns:
        Environment variables for the job, one per line.

    Examples:
        jobenv(19804935)
    """
    _validate_job_id(job_id)
    executor = get_executor()
    result = executor.run(['jobenv', str(job_id)])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get job environment: {result.stderr}')

    return result.stdout


@mcp_tool
def jobscript(job_id: int) -> str:
    """
    Get the full submission script for a Slurm job.

    Returns the complete batch script that was submitted, including
    all SBATCH directives and commands.

    Args:
        job_id: The Slurm job ID to query.

    Returns:
        The full job submission script content.

    Examples:
        jobscript(19804935)
    """
    _validate_job_id(job_id)
    executor = get_executor()
    result = executor.run(['jobscript', str(job_id)])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get job script: {result.stderr}')

    return result.stdout


@mcp_tool
def showpartitions() -> str:
    """
    Show available partitions and their current status.

    Displays partition statistics including node counts, CPU cores,
    pending jobs, node limits, max job time, cores per node, and
    memory per node.

    Returns:
        Partition statistics table showing availability and limits.

    Examples:
        showpartitions()
    """
    executor = get_executor()
    result = executor.run(['showpartitions'])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get partition info: {result.stderr}')

    return result.stdout


@mcp_tool
def average_wait(
    partition: Optional[str] = None,
    account: Optional[str] = None,
) -> str:
    """
    Show aggregate statistics of queue wait times.

    Displays wait time statistics for jobs, optionally filtered by
    partition or account.

    Args:
        partition: Filter by partition name (e.g., 'cpu', 'gpu').
        account: Filter by Slurm account.

    Returns:
        Queue wait time statistics.

    Examples:
        average_wait()
        average_wait(partition='gpu')
        average_wait(account='mylab')
    """
    executor = get_executor()

    # average_wait passes these on to sacct, whose partition option is --partition (-p there is
    # --parsable).
    argv = ['average_wait']
    # An empty value was not given, as for the Slurm tools' options.
    if partition:
        argv += ['--partition', partition]
    if account:
        argv += ['-A', account]

    result = executor.run(argv)

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get wait time stats: {result.stderr}')

    return result.stdout


@dataclass
class StorageSpace:
    """Information about a storage space."""

    type: str
    """Storage type: 'home', 'scratch', or 'depot'."""

    name: str
    """Location name from myquota (username or group name)."""

    path: str
    """Full filesystem path to the storage space."""

    size: str
    """Current usage (e.g., '14.6GB')."""

    limit: str
    """Quota limit (e.g., '25.0GB')."""

    usage_percent: str
    """Usage as percentage (e.g., '58.6%')."""


@dataclass
class StoragePaths:
    """Resolved storage paths for the current user."""

    home: StorageSpace
    """User's home directory."""

    scratch: StorageSpace
    """User's scratch space."""

    depots: List[StorageSpace]
    """List of depot spaces the user has access to."""


def _parse_myquota_output(output: str, scratch_path: str) -> StoragePaths:
    """
    Parse myquota output into structured StoragePaths.

    Args:
        output: Raw myquota command output.
        scratch_path: Full scratch path from findscratch (e.g., /scratch/gautschi/glentner).

    Returns:
        StoragePaths with resolved home, scratch, and depot spaces.
    """
    home = None
    scratch = None
    depots: List[StorageSpace] = []

    for line in output.strip().split('\n'):
        # Skip header lines
        if not line or line.startswith('Type') or line.startswith('==='):
            continue

        parts = line.split()
        if len(parts) < 6:
            continue

        storage_type = parts[0]
        location = parts[1]
        size = parts[2]
        limit = parts[3]
        usage_pct = parts[4]

        if storage_type == 'home':
            home = StorageSpace(
                type='home',
                name=location,
                path=f'/home/{location}',
                size=size,
                limit=limit,
                usage_percent=usage_pct,
            )
        elif storage_type == 'scratch':
            # Use the authoritative scratch path from findscratch
            scratch = StorageSpace(
                type='scratch',
                name=location,
                path=scratch_path,
                size=size,
                limit=limit,
                usage_percent=usage_pct,
            )
        elif storage_type == 'depot':
            depots.append(StorageSpace(
                type='depot',
                name=location,
                path=f'/depot/{location}',
                size=size,
                limit=limit,
                usage_percent=usage_pct,
            ))

    if home is None:
        raise RuntimeError('No home directory found in myquota output')
    if scratch is None:
        raise RuntimeError('No scratch space found in myquota output')

    return StoragePaths(home=home, scratch=scratch, depots=depots)


@mcp_tool
def storage_paths() -> StoragePaths:
    """
    Get resolved storage paths for the current user.

    Returns the actual filesystem paths for all storage spaces the user
    has access to, including home, scratch, and all depot allocations.
    This is the recommended way to discover storage locations when a user
    mentions "scratch", "depot", or "home".

    Returns:
        StoragePaths object with:
        - home: User's home directory (/home/<user>)
        - scratch: User's scratch space (/scratch/<cluster>/<user>)
        - depots: List of depot spaces (/depot/<group>) the user can access

    Examples:
        storage_paths()
    """
    executor = get_executor()

    # Get the actual scratch path using findscratch (authoritative source)
    # This returns the full path like /scratch/gautschi/glentner
    scratch_result = executor.run(['findscratch'])
    if scratch_result.exit_code != 0 or not scratch_result.stdout.strip():
        raise RuntimeError(f'Failed to get scratch path: {scratch_result.stderr}')
    scratch_path = scratch_result.stdout.strip()

    # Get myquota output
    quota_result = executor.run(['myquota'])
    if quota_result.exit_code != 0:
        raise RuntimeError(f'Failed to get quota info: {quota_result.stderr}')

    return _parse_myquota_output(quota_result.stdout, scratch_path)
