# SPDX-FileCopyrightText: 2026 Purdue University
# SPDX-License-Identifier: MIT

"""The served surface matches the snapshot taken from the server before the stack move.

`tests/fixtures/served_surface.json` is what the server at `3d010ec` (FastMCP 2.x) served to a
handshake-era client: every tool's description and input and output schemas, every resource's URI,
name, description and MIME type, and the instructions. It was written by
`spec/stack-upgrade/verify/snapshot.py` run in an archive of that commit, in that commit's own
locked environment, never on the current stack; the cycle's gates re-derive it there and require
the committed file to be identical. A deliberate surface change since then is a scripted hand-edit
of the named deltas only, in the snapshot's shape, listed in AGENTS.md § Testing: so far
`run_command.description`, `write_file.description`, `write_file.properties.encoding`,
`sbatch.description`, `sbatch.properties.args`, and `description` and `properties.extra_args` on
each of `sbatch`, `squeue`, `scancel`, `sacct` and `sinfo` (`safe-execution`); `instructions`, the
base composed with the harness's site context, `tools.cluster_context`, the new tool in `myquota`'s
schema shape with its docstring as the description, and `resources.rcac://context.description`
(`agents-d-instructions`).

`surface_violations` compares what a client in the selected era is served now (`served_surface`)
with the snapshot. No tool, argument, required argument, argument type or default, description,
resource or instruction may differ. Four kinds of generated schema metadata differ on FastMCP 4,
each for a stated cause (`TOLERATED`), and each is accepted only in its own shape: a `description`
added to an argument that had none; `additionalProperties` added to an input schema, and only as
exactly `false`; an output schema's `$defs` inlined where they were referenced; and a `description`
added on an inlined output node, only where the snapshot had a `$ref`. Anything else is a violation.
`tolerated_differences` lists the accepted differences per tool, for the record of the change.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from harness import SITE, served_surface
from rcac_mcp.server import create_mcp_server
from test_registry import EXPECTED_TOOLS

FIXTURE = Path(__file__).resolve().parent / 'fixtures' / 'served_surface.json'

# Each accepted difference kind, with its cause.
TOLERATED: dict[str, str] = {
    'argument-description': "FastMCP 4 copies each docstring `Args:` line into that argument's schema as its "
                            '`description`',
    'additional-properties': 'FastMCP 4 advertises `additionalProperties: false`, the rejection of an unknown '
                             'argument that both stacks already enforce',
    'output-defs-inlined': "FastMCP 4 dereferences an output schema's `$defs`, inlining each model where it "
                           'was referenced',
    'output-model-description': "inlining carries a model class's docstring onto the node that was its `$ref`",
}

DEFS_PREFIX = '#/$defs/'

NodePath = tuple[Any, ...]


def load_fixture() -> dict[str, Any]:
    """The snapshot of the surface served before the stack move."""
    return json.loads(FIXTURE.read_text())


def inline_defs(schema: Any) -> tuple[Any, set[NodePath]]:
    """`schema` with each local `$ref` replaced by its `$defs` entry and `$defs` dropped.

    Also returns the paths (key and index tuples from the root) of the nodes that were a `$ref`. A
    schema without `$defs` comes back as a copy, with no paths. A model that refers to itself keeps
    its inner `$ref`.
    """
    if not isinstance(schema, dict) or '$defs' not in schema:
        return copy.deepcopy(schema), set()
    defs = schema['$defs']
    targets: set[NodePath] = set()

    def walk(node: Any, path: NodePath, active: frozenset[str]) -> Any:
        if isinstance(node, dict):
            ref = node.get('$ref')
            if isinstance(ref, str) and ref.startswith(DEFS_PREFIX):
                name = ref[len(DEFS_PREFIX):]
                if name in defs and name not in active:
                    targets.add(path)
                    merged = {**defs[name], **{k: v for k, v in node.items() if k != '$ref'}}
                    return walk(merged, path, active | {name})
            return {k: walk(v, path + (k,), active) for k, v in node.items() if not (path == () and k == '$defs')}
        if isinstance(node, list):
            return [walk(v, path + (i,), active) for i, v in enumerate(node)]
        return copy.deepcopy(node)

    return walk(schema, (), frozenset()), targets


def _where(path: NodePath) -> str:
    return '.'.join(str(p) for p in path) or '(root)'


class _Comparison:
    """Every violation, and every tolerated difference per tool, between a snapshot and a served surface."""

    def __init__(self) -> None:
        self.violations: list[str] = []
        self.tolerated: dict[str, list[str]] = {}

    def tolerate(self, tool: str, kind: str, detail: str = '') -> None:
        assert kind in TOLERATED, kind
        self.tolerated.setdefault(tool, []).append(f'{kind}: {detail}' if detail else kind)

    def surface(self, snapshot: dict[str, Any], served: dict[str, Any]) -> None:
        if snapshot.get('instructions') != served.get('instructions'):
            self.violations.append('instructions differ')
        base_tools, head_tools = snapshot.get('tools', {}), served.get('tools', {})
        for name in sorted(set(base_tools) - set(head_tools)):
            self.violations.append(f'tool missing: {name}')
        for name in sorted(set(head_tools) - set(base_tools)):
            self.violations.append(f'tool added: {name}')
        for name in sorted(set(base_tools) & set(head_tools)):
            self.tool(name, base_tools[name], head_tools[name])
        self.resources(snapshot.get('resources', {}), served.get('resources', {}))

    def tool(self, name: str, base: dict[str, Any], head: dict[str, Any]) -> None:
        for key in sorted(set(base) ^ set(head)):
            self.violations.append(f'{name}: tool entry {"lost" if key in base else "gained"} {key!r}')
        if base.get('description') != head.get('description'):
            self.violations.append(f'{name}: description differs')
        self.input_schema(name, base.get('inputSchema') or {}, head.get('inputSchema') or {})
        self.output_schema(name, base.get('outputSchema'), head.get('outputSchema'))

    def input_schema(self, tool: str, base: dict[str, Any], head: dict[str, Any]) -> None:
        for key in sorted(set(base) | set(head)):
            if key in ('properties', 'required'):
                continue
            if key == 'additionalProperties' and key not in base:
                if head[key] is False:
                    self.tolerate(tool, 'additional-properties')
                else:
                    self.violations.append(f'{tool}: additionalProperties is {head[key]!r}, expected false')
            elif key not in base:
                self.violations.append(f'{tool}: input schema gained untolerated key {key!r}')
            elif key not in head:
                self.violations.append(f'{tool}: input schema lost key {key!r}')
            elif base[key] != head[key]:
                self.violations.append(f'{tool}: input schema {key} differs: {base[key]!r} -> {head[key]!r}')
        if base.get('required', []) != head.get('required', []):
            self.violations.append(f'{tool}: required differs: {base.get("required", [])} -> {head.get("required", [])}')
        base_props, head_props = base.get('properties', {}), head.get('properties', {})
        for arg in sorted(set(base_props) - set(head_props)):
            self.violations.append(f'{tool}: argument dropped: {arg}')
        for arg in sorted(set(head_props) - set(base_props)):
            self.violations.append(f'{tool}: argument added: {arg}')
        described: list[str] = []
        for arg in [a for a in head_props if a in base_props]:
            old, new = base_props[arg], head_props[arg]
            for key in sorted(set(old) | set(new)):
                if key == 'description' and key not in old and isinstance(new[key], str):
                    described.append(arg)
                elif key not in old:
                    self.violations.append(f'{tool}: argument {arg} gained untolerated key {key!r}')
                elif key not in new:
                    self.violations.append(f'{tool}: argument {arg} lost {key!r}')
                elif old[key] != new[key]:
                    self.violations.append(f'{tool}: argument {arg} {key} differs: {old[key]!r} -> {new[key]!r}')
        if described:
            self.tolerate(tool, 'argument-description', ', '.join(described))

    def output_schema(self, tool: str, base: Any, head: Any) -> None:
        if base is None or head is None:
            if base != head:
                self.violations.append(f'{tool}: output schema differs: {base!r} -> {head!r}')
            return
        inlined, targets = inline_defs(base)
        served, _ = inline_defs(head)
        if '$defs' in base and '$defs' not in head:
            self.tolerate(tool, 'output-defs-inlined', ', '.join(sorted(base['$defs'])))
        self.output_node(tool, inlined, served, (), targets)

    def output_node(self, tool: str, base: Any, head: Any, path: NodePath, targets: set[NodePath]) -> None:
        if isinstance(base, dict) and isinstance(head, dict):
            for key in sorted(set(base) | set(head)):
                if key not in base:
                    if key == 'description' and path in targets and isinstance(head[key], str):
                        self.tolerate(tool, 'output-model-description', _where(path))
                    else:
                        self.violations.append(f'{tool}: output schema at {_where(path)} gained untolerated key {key!r}')
                elif key not in head:
                    self.violations.append(f'{tool}: output schema at {_where(path)} lost {key!r}')
                else:
                    self.output_node(tool, base[key], head[key], path + (key,), targets)
        elif isinstance(base, list) and isinstance(head, list) and len(base) == len(head):
            for i, (old, new) in enumerate(zip(base, head)):
                self.output_node(tool, old, new, path + (i,), targets)
        elif base != head:
            self.violations.append(f'{tool}: output schema at {_where(path)} differs: {base!r} -> {head!r}')

    def resources(self, base: dict[str, Any], head: dict[str, Any]) -> None:
        for uri in sorted(set(base) - set(head)):
            self.violations.append(f'resource missing: {uri}')
        for uri in sorted(set(head) - set(base)):
            self.violations.append(f'resource added: {uri}')
        for uri in sorted(set(base) & set(head)):
            for key in sorted(set(base[uri]) | set(head[uri])):
                if base[uri].get(key) != head[uri].get(key):
                    self.violations.append(f'resource {uri}: {key} differs')


def _compare(snapshot: dict[str, Any], served: dict[str, Any]) -> _Comparison:
    comparison = _Comparison()
    comparison.surface(snapshot, served)
    return comparison


def surface_violations(snapshot: dict[str, Any], served: dict[str, Any]) -> list[str]:
    """Every way `served` differs from `snapshot` that no `TOLERATED` kind accounts for."""
    return _compare(snapshot, served).violations


def tolerated_differences(snapshot: dict[str, Any], served: dict[str, Any]) -> dict[str, list[str]]:
    """Per tool, each accepted difference between `snapshot` and `served`: `<kind>[: <detail>]`."""
    return _compare(snapshot, served).tolerated


@pytest.mark.unit
def test_served_surface_matches_baseline() -> None:
    served = served_surface(create_mcp_server('none', site=SITE))
    assert served['tools'], 'the server listed no tools'
    violations = surface_violations(load_fixture(), served)
    assert violations == [], '\n'.join(violations)


@pytest.mark.unit
def test_surface_fixture_is_the_baseline_shape() -> None:
    snapshot = load_fixture()
    assert sorted(snapshot) == ['instructions', 'resources', 'tools']
    assert isinstance(snapshot['instructions'], str) and snapshot['instructions']
    assert sorted(snapshot['tools']) == sorted(EXPECTED_TOOLS)
    assert sorted(snapshot['resources']) == ['rcac://context', 'rcac://storage']
    for name, tool in snapshot['tools'].items():
        assert sorted(tool) == ['description', 'inputSchema', 'outputSchema'], name
        assert isinstance(tool['description'], str) and tool['description'], name
        # The metadata FastMCP 4 adds is absent: the snapshot was not taken on the new stack.
        assert 'additionalProperties' not in tool['inputSchema'], name
        assert all('description' not in arg for arg in tool['inputSchema'].get('properties', {}).values()), name
    # The output models are referenced through `$defs`, as FastMCP 2.x served them.
    assert any('$defs' in (tool['outputSchema'] or {}) for tool in snapshot['tools'].values())


def _plant(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    planted = load_fixture()
    change(planted)
    return planted


def _rename(s: dict[str, Any]) -> None:
    s['tools']['run_command_v2'] = s['tools'].pop('run_command')


def _input(s: dict[str, Any]) -> dict[str, Any]:
    return s['tools']['run_command']['inputSchema']


def _summary_only(s: dict[str, Any]) -> None:
    tool = s['tools']['run_command']
    tool['description'] = tool['description'].split('\n\n')[0]


@pytest.mark.unit
@pytest.mark.parametrize(('change', 'fragment'), [
    pytest.param(_rename, 'tool missing: run_command', id='renamed-tool'),
    pytest.param(lambda s: _input(s)['properties'].pop('cwd'), 'run_command: argument dropped: cwd',
                 id='dropped-argument'),
    pytest.param(lambda s: _input(s).update(required=[]), 'run_command: required differs', id='required-changed'),
    pytest.param(lambda s: _input(s)['properties']['command'].update(type='integer'),
                 'run_command: argument command type differs', id='type-changed'),
    pytest.param(lambda s: _input(s)['properties']['cwd'].update(default='~'),
                 'run_command: argument cwd default differs', id='default-changed'),
    pytest.param(_summary_only, 'run_command: description differs', id='description-changed'),
    pytest.param(lambda s: s['resources'].pop('rcac://storage'), 'resource missing: rcac://storage',
                 id='resource-dropped'),
    pytest.param(lambda s: s.update(instructions=s['instructions'] + '\nUse the docs tool.'), 'instructions differ',
                 id='instructions-changed'),
    pytest.param(lambda s: s['tools']['run_command']['outputSchema']['properties']['exit_code'].update(type='string'),
                 'run_command: output schema at properties.exit_code.type differs', id='output-changed'),
    pytest.param(lambda s: _input(s).update(title='run_commandArguments'),
                 "run_command: input schema gained untolerated key 'title'", id='untolerated-key'),
    pytest.param(lambda s: _input(s).update(additionalProperties=True),
                 'run_command: additionalProperties is True, expected false', id='additional-properties-true'),
    pytest.param(lambda s: s['tools']['run_command']['outputSchema']['properties']['stdout'].update(description='Out.'),
                 "run_command: output schema at properties.stdout gained untolerated key 'description'",
                 id='output-description-on-a-plain-node'),
])
def test_surface_check_catches_drift(change: Callable[[dict[str, Any]], None], fragment: str) -> None:
    snapshot = load_fixture()
    assert surface_violations(snapshot, load_fixture()) == []
    violations = surface_violations(snapshot, _plant(change))
    assert any(fragment in v for v in violations), violations


def _describe_argument(s: dict[str, Any]) -> None:
    _input(s)['properties']['command']['description'] = 'The shell command to execute.'


def _inline_listing(s: dict[str, Any]) -> None:
    tool = s['tools']['list_directory']
    tool['outputSchema'] = inline_defs(tool['outputSchema'])[0]


def _describe_model(s: dict[str, Any]) -> None:
    _inline_listing(s)
    s['tools']['list_directory']['outputSchema']['properties']['result']['items']['description'] = 'One entry.'


@pytest.mark.unit
@pytest.mark.parametrize(('change', 'tool', 'kind'), [
    pytest.param(_describe_argument, 'run_command', 'argument-description: command', id='argument-description'),
    pytest.param(lambda s: _input(s).update(additionalProperties=False), 'run_command', 'additional-properties',
                 id='additional-properties'),
    pytest.param(_inline_listing, 'list_directory', 'output-defs-inlined: FileInfo', id='output-defs-inlined'),
    pytest.param(_describe_model, 'list_directory', 'output-model-description: properties.result.items',
                 id='output-model-description'),
])
def test_surface_check_tolerates_listed_differences(change: Callable[[dict[str, Any]], None], tool: str,
                                                    kind: str) -> None:
    snapshot = load_fixture()
    planted = _plant(change)
    assert planted != snapshot, 'the planted difference did not land'
    assert surface_violations(snapshot, planted) == []
    tolerated = tolerated_differences(snapshot, planted)
    assert kind in tolerated.get(tool, []), tolerated
    assert all(entry.split(':')[0] in TOLERATED for entries in tolerated.values() for entry in entries)
