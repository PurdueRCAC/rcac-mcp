"""Require every test id listed in IDS (one per line, `#` comments allowed) to be a passed testcase in REPORT.

    python junit_ids.py REPORT IDS

Passed means a <testcase> with no <failure>, <error> or <skipped> child. The report also must hold
nothing failed, errored or skipped anywhere, so a named id cannot pass beside a red sibling.
"""

import sys
import xml.etree.ElementTree as ET

report, ids = sys.argv[1:3]
want = [line.split('#')[0].strip() for line in open(ids)]
want = [w for w in want if w]
state = {}
for case in ET.parse(report).iter('testcase'):
    tags = {child.tag for child in case}
    state[case.get('name')] = next((t for t in ('failure', 'error', 'skipped') if t in tags), 'passed')
bad = [f'{w}: {state.get(w, "absent")}' for w in want if state.get(w) != 'passed']
bad += [f'{n}: {s}' for n, s in state.items() if s != 'passed' and n not in want]
for line in bad:
    print(f'junit_ids: {line}', file=sys.stderr)
print(f'junit_ids: {len(want)} named ids passed' if not bad else f'junit_ids: {len(bad)} problem(s)')
sys.exit(1 if bad or not want else 0)
