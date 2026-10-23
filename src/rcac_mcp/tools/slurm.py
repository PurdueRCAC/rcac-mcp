# SPDX-FileCopyrightText: 2025 Purdue University
# SPDX-License-Identifier: MIT

"""Slurm workload manager tools.

These tools provide interfaces to standard Slurm commands for job submission,
monitoring, and cluster status queries. Also includes RCAC-specific Slurm
extensions like slist and sfeatures.

Every tool hands the executor an argv: each option value is its own element, so it reaches the
program as exactly one literal argument, and a positional value follows `--`.
"""

# Type annotations
from __future__ import annotations
from typing import Optional, List
import re
import secrets
from datetime import datetime

# Internal libs
from rcac_mcp.tools import mcp_tool
from rcac_mcp.context import get_executor
from rcac_mcp.executor import CONNECTION_LOST_EXIT_CODE, UserPath


# Default directory for job scripts
DEFAULT_JOBS_DIR = '~/jobs'

# The characters a job script's filename keeps from the job name; any other becomes `_`.
UNSAFE_NAME = re.compile(r'[^A-Za-z0-9._-]')


def _validate_job_id(job_id: int) -> None:
    """Validate that job_id is a positive integer."""
    if not isinstance(job_id, int) or job_id <= 0:
        raise ValueError(f'job_id must be a positive integer, got: {job_id}')


def _options(*pairs: tuple[str, Optional[str]]) -> List[str]:
    """Each (option, value) pair whose value was given, as two elements: the option, then the value.

    An optional value that is empty was not given, as if it were None: some clients send `''` for
    an optional string they leave unset, and an option with an empty value is never what is meant.
    """
    argv: List[str] = []
    for option, value in pairs:
        if value:
            argv += [option, value]
    return argv


def _extra(extra_args: Optional[List[str]]) -> List[str]:
    """The caller's extra options, one argument per element; an element "--" is refused."""
    if not extra_args:
        return []
    if '--' in extra_args:
        raise ValueError('extra_args must not contain "--": each element is an option for the program, '
                         'and "--" would end its options')
    return list(extra_args)


def _job_script_path(job_name: Optional[str]) -> UserPath:
    """Where a job script written from content goes: ~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh.

    The name and the second keep scripts findable by job name and sorted by time. The token is 16
    hex digits drawn from the operating system's entropy source on every call, so no other call
    draws the same path: not one in the same second, not one in another server process, and not
    one on another host sharing this home. A counter, a seeded generator, the clock, the process
    id or the host name could each repeat across processes or hosts, and two calls on one path
    would overwrite each other's script.
    """
    name = UNSAFE_NAME.sub('_', job_name or '').lstrip('.-') or 'job'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    token = secrets.token_hex(8)
    return UserPath(f'{DEFAULT_JOBS_DIR}/{name}_{timestamp}_{token}.sh')


@mcp_tool
def sbatch(
    script_path: Optional[str] = None,
    script_content: Optional[str] = None,
    args: Optional[List[str]] = None,
    job_name: Optional[str] = None,
    account: Optional[str] = None,
    partition: Optional[str] = None,
    qos: Optional[str] = None,
    time: Optional[str] = None,
    nodes: Optional[int] = None,
    ntasks: Optional[int] = None,
    cpus_per_task: Optional[int] = None,
    mem: Optional[str] = None,
    gpus: Optional[str] = None,
    constraint: Optional[str] = None,
    output: Optional[str] = None,
    error: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> str:
    """
    Submit a batch job to Slurm.

    Either script_path (existing file on cluster) or script_content
    (script text to write) must be provided. If script_content is given,
    the script is written byte for byte (no newline added) to
    ~/jobs/<name>_<YYYYmmdd_HHMMSS>_<token>.sh (<name>: the sanitised job_name;
    <token>: 16 random hex digits, new per call) and made executable before submission.
    Every value reaches sbatch, or the job script, as exactly one argument.

    Args:
        script_path: Path to existing job script on the cluster (one literal path).
        script_content: Job script content to write and submit.
        args: Arguments for the job script, one per element, passed after its path.
        job_name: Job name (-J/--job-name). Used in script filename if writing.
        account: Slurm account to charge (-A/--account).
        partition: Partition to submit to (-p/--partition).
        qos: Quality of service (-q/--qos), e.g., 'standby'.
        time: Wall time limit (-t/--time), e.g., '01:00:00'.
        nodes: Number of nodes (-N/--nodes).
        ntasks: Number of tasks (-n/--ntasks).
        cpus_per_task: CPUs per task (-c/--cpus-per-task).
        mem: Memory per node (--mem), e.g., '4G'.
        gpus: GPU specification (--gpus), e.g., '1' or 'mi210:2'.
        constraint: Node constraint (-C/--constraint).
        output: Stdout file path (-o/--output).
        error: Stderr file path (-e/--error).
        extra_args: Additional sbatch options, one argument per element (e.g. ['--exclusive']),
            placed after the options above and before the script path; an element '--' is refused.

    Returns:
        The submitted job ID as a string.

    Examples:
        sbatch(script_path='~/jobs/my_job.sh')
        sbatch(script_path='~/jobs/sweep.sh', args=['--lr', '0.01', 'run one'])
        sbatch(
            script_content='#!/bin/bash\\n#SBATCH -c 4\\npython train.py\\n',
            job_name='train',
            account='mylab',
            partition='gpu',
            time='02:00:00',
            extra_args=['--mail-type', 'END'],
        )
    """
    # An empty script_path was not given, as for the options below; script_content is content, so
    # an empty one is an empty script.
    if not script_path and script_content is None:
        raise ValueError('Either script_path or script_content must be provided')

    if script_path and script_content is not None:
        raise ValueError('Provide either script_path or script_content, not both')

    # Every refusal comes before any executor call: nothing is written for a job that cannot be
    # submitted.
    options = _extra(extra_args)
    data = None
    if script_content is not None:
        try:
            data = script_content.encode('utf-8')
        except UnicodeEncodeError:
            raise ValueError('script_content is not valid UTF-8 text: it contains a lone surrogate') from None

    executor = get_executor()

    if data is not None:
        path = _job_script_path(job_name)
        # The content goes through the executor's write, as write_file's does, so the script holds
        # exactly the bytes sent; then it is made executable.
        try:
            written = executor.write(path, data, parent=DEFAULT_JOBS_DIR, executable=True)
        except OSError as exc:
            raise RuntimeError(f'Failed to write job script: {exc}') from None
        if written.exit_code != 0:
            raise RuntimeError(f'Failed to write job script (exit {written.exit_code}): {written.stderr}')
    else:
        path = UserPath(script_path)

    argv = ['sbatch']
    argv += _options(('-J', job_name), ('-A', account), ('-p', partition), ('-q', qos), ('-t', time))
    if nodes:
        argv += ['-N', str(nodes)]
    if ntasks:
        argv += ['-n', str(ntasks)]
    if cpus_per_task:
        argv += ['-c', str(cpus_per_task)]
    argv += _options(('--mem', mem), ('--gpus', gpus), ('-C', constraint))
    argv += _options(('-o', UserPath(output) if output else None), ('-e', UserPath(error) if error else None))
    # The script path is a UserPath, so it is absolute or starts with `./`: even when an extra
    # option that takes a value consumes the `--`, the path is never read as an option.
    argv += [*options, '--', path, *(args or [])]

    result = executor.run(argv)

    # A lost connection is never a failed submission: sbatch may have run, and submitting again
    # could run the job twice.
    if result.exit_code == CONNECTION_LOST_EXIT_CODE:
        raise RuntimeError('The connection was lost during the submission, so the job may have been submitted: '
                           f'check squeue before submitting it again. {result.stdout}{result.stderr}'.rstrip())
    if result.exit_code != 0:
        raise RuntimeError(f'Failed to submit job: {result.stderr}')

    # Extract job ID from output like "Submitted batch job 19804935"
    match = re.search(r'Submitted batch job (\d+)', result.stdout)
    if match:
        return match.group(1)

    return result.stdout.strip()


@mcp_tool
def squeue(
    user: Optional[str] = None,
    account: Optional[str] = None,
    partition: Optional[str] = None,
    state: Optional[str] = None,
    job_ids: Optional[List[int]] = None,
    me: bool = True,
    extra_args: Optional[List[str]] = None,
) -> str:
    """
    View the Slurm job queue.

    By default shows only the current user's jobs (--me). Set me=False
    and provide user/account to see other jobs.

    Args:
        user: Filter by username (-u/--user).
        account: Filter by account (-A/--account).
        partition: Filter by partition (-p/--partition).
        state: Filter by job state (-t/--states), e.g., 'PENDING', 'RUNNING'.
        job_ids: Specific job IDs to query (-j/--jobs).
        me: Show only current user's jobs (default: True).
        extra_args: Additional squeue options, one argument per element (e.g. ['--long']),
            placed after the options above; an element '--' is refused.

    Returns:
        Job queue listing.

    Examples:
        squeue()
        squeue(state='PENDING')
        squeue(me=False, account='mylab')
        squeue(job_ids=[19804935, 19804936])
        squeue(extra_args=['--long'])
    """
    options = _extra(extra_args)
    executor = get_executor()

    argv = ['squeue']

    # Default to --me unless other filters specified
    if me and not user and not account:
        argv.append('--me')
    argv += _options(('-u', user), ('-A', account), ('-p', partition), ('-t', state))
    if job_ids:
        argv += ['-j', ','.join(str(j) for j in job_ids)]
    argv += options

    result = executor.run(argv)

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to query queue: {result.stderr}')

    return result.stdout


@mcp_tool
def scancel(
    job_ids: Optional[List[int]] = None,
    user: Optional[str] = None,
    account: Optional[str] = None,
    partition: Optional[str] = None,
    state: Optional[str] = None,
    name: Optional[str] = None,
    me: bool = False,
    extra_args: Optional[List[str]] = None,
) -> str:
    """
    Cancel Slurm jobs.

    Provide job_ids for specific jobs, or use filters to cancel matching jobs.
    Use me=True to cancel all your jobs (equivalent to scancel --me).

    Args:
        job_ids: Specific job IDs to cancel.
        user: Cancel jobs for this user (-u/--user).
        account: Cancel jobs under this account (-A/--account).
        partition: Cancel jobs in this partition (-p/--partition).
        state: Cancel jobs in this state (-t/--state).
        name: Cancel jobs with this name (-n/--name).
        me: Cancel all current user's jobs (--me).
        extra_args: Additional scancel options, one argument per element (e.g. ['--signal', 'USR1']),
            placed after the options above; an element '--' is refused.

    Returns:
        Confirmation message or empty string on success.

    Examples:
        scancel(job_ids=[19804935])
        scancel(me=True)
        scancel(state='PENDING', account='mylab')
    """
    if not job_ids and not me and not user and not account and not name:
        raise ValueError('Must specify job_ids, me=True, or a filter (user/account/name)')

    options = _extra(extra_args)
    executor = get_executor()

    argv = ['scancel']
    if me:
        argv.append('--me')
    argv += _options(('-u', user), ('-A', account), ('-p', partition), ('-t', state), ('-n', name))
    argv += options
    if job_ids:
        argv += ['--', *(str(j) for j in job_ids)]

    result = executor.run(argv)

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to cancel jobs: {result.stderr}')

    return result.stdout if result.stdout.strip() else 'Jobs cancelled successfully'


@mcp_tool
def sacct(
    user: Optional[str] = None,
    account: Optional[str] = None,
    job_ids: Optional[List[int]] = None,
    name: Optional[str] = None,
    state: Optional[str] = None,
    starttime: Optional[str] = None,
    endtime: Optional[str] = None,
    format: Optional[str] = None,
    me: bool = True,
    extra_args: Optional[List[str]] = None,
) -> str:
    """
    Query Slurm job accounting history.

    Shows completed and running jobs from the accounting database.
    By default shows only the current user's jobs with -X (no sub-steps).

    Args:
        user: Filter by username (-u/--user).
        account: Filter by account (-A/--account).
        job_ids: Specific job IDs to query (-j/--jobs).
        name: Filter by job name (--name).
        state: Filter by state (-s/--state), e.g., 'COMPLETED', 'FAILED'.
        starttime: Start of time range (-S/--starttime), e.g., '2024-01-01'.
        endtime: End of time range (-E/--endtime).
        format: Output format (-o/--format), e.g., 'JobID,JobName,State,ExitCode'.
        me: Show only current user's jobs (default: True).
        extra_args: Additional sacct options, one argument per element (e.g. ['--parsable2']),
            placed after the options above; an element '--' is refused.

    Returns:
        Job accounting information.

    Examples:
        sacct()
        sacct(starttime='2024-01-01', state='FAILED')
        sacct(job_ids=[19804935])
        sacct(format='JobID,JobName,State,Elapsed,MaxRSS')
    """
    options = _extra(extra_args)
    executor = get_executor()

    argv = ['sacct', '-X']  # Always exclude sub-job steps for cleaner output

    if me and not user and not account:
        # Get current user
        user_result = executor.run(['whoami'])
        current_user = user_result.stdout.strip()
        argv += ['-u', current_user]
    argv += _options(('-u', user), ('-A', account))
    if job_ids:
        argv += ['-j', ','.join(str(j) for j in job_ids)]
    argv += _options(('--name', name), ('-s', state), ('-S', starttime), ('-E', endtime), ('-o', format))
    argv += options

    result = executor.run(argv)

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to query job history: {result.stderr}')

    return result.stdout


@mcp_tool
def sinfo(
    partition: Optional[str] = None,
    nodes: Optional[str] = None,
    state: Optional[str] = None,
    format: Optional[str] = None,
    extra_args: Optional[List[str]] = None,
) -> str:
    """
    Show cluster and partition status.

    Displays information about nodes and partitions including
    availability, state, and resources.

    Args:
        partition: Filter by partition (-p/--partition).
        nodes: Filter by node list (-n/--nodes).
        state: Filter by node state (-t/--states), e.g., 'idle', 'alloc'.
        format: Output format (-o/--format).
        extra_args: Additional sinfo options, one argument per element (e.g. ['--Node', '--long']),
            placed after the options above; an element '--' is refused.

    Returns:
        Cluster/partition status information.

    Examples:
        sinfo()
        sinfo(partition='gpu')
        sinfo(state='idle')
    """
    options = _extra(extra_args)
    executor = get_executor()

    argv = ['sinfo']
    argv += _options(('-p', partition), ('-n', nodes), ('-t', state), ('-o', format))
    argv += options

    result = executor.run(argv)

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get cluster info: {result.stderr}')

    return result.stdout


@mcp_tool
def scontrol_show_job(job_id: int) -> str:
    """
    Show detailed Slurm job information.

    Returns comprehensive job details directly from Slurm's control
    daemon, including all job parameters and current state.

    Args:
        job_id: The Slurm job ID to query.

    Returns:
        Detailed job information from scontrol.

    Examples:
        scontrol_show_job(19804935)
    """
    _validate_job_id(job_id)
    executor = get_executor()

    result = executor.run(['scontrol', 'show', 'job', str(job_id)])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get job details: {result.stderr}')

    return result.stdout


@mcp_tool
def scontrol_show_node(node: Optional[str] = None) -> str:
    """
    Show detailed node information for diagnostics.

    Returns comprehensive node details from Slurm including
    state, resources, features, and current load.

    Args:
        node: Specific node name to query. If None, shows all nodes.

    Returns:
        Detailed node information from scontrol.

    Examples:
        scontrol_show_node('a000')
        scontrol_show_node()  # All nodes (may be large output)
    """
    executor = get_executor()

    argv = ['scontrol', 'show', 'node']
    # An empty name was not given, as for the Slurm tools' options: every node is shown.
    if node:
        # After `--`, the name is never read as an scontrol option.
        argv += ['--', node]

    result = executor.run(argv)

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get node details: {result.stderr}')

    return result.stdout


@mcp_tool
def slist() -> str:
    """
    Show Slurm accounts and current usage (RCAC-specific).

    Displays accounts the user has access to submit jobs with,
    along with current usage relative to allocation limits.
    Shows CPU partition usage and AI/GPU partition balance.

    Returns:
        Account listing with usage statistics.

    Examples:
        slist()
    """
    executor = get_executor()
    result = executor.run(['slist'])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get account list: {result.stderr}')

    return result.stdout


@mcp_tool
def sfeatures() -> str:
    """
    Show available node features and constraints (RCAC-specific).

    Displays node hardware features that can be used as constraints
    when submitting jobs, including CPU types, memory tiers, and
    GPU models.

    Returns:
        Node features listing with CPUS, MEMORY, AVAIL_FEATURES, and GRES.

    Examples:
        sfeatures()
    """
    executor = get_executor()
    result = executor.run(['sfeatures'])

    if result.exit_code != 0:
        raise RuntimeError(f'Failed to get node features: {result.stderr}')

    return result.stdout
