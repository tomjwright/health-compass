"""Fails when the tests killed fewer than FLOOR percent of the mutants in any mutated file.

Reads `mutmut results --all true` on stdin, run from the repo root after `mutmut run`:

    uv run mutmut results --all true | python3 scripts/mutation_floor.py 85
"""

import re
import sys
from collections import Counter

# Mutant names look like agent_template.pricing.x_cost_usd__mutmut_1 or
# agent_template.orchestrator.xǁOrchestratorǁrun__mutmut_4; the part before ".x_" or ".xǁ"
# is the module.
MUTANT = re.compile(r"^(?P<module>[\w.]+?)\.x[_ǁ].*__mutmut_\d+$")
KILLED = {"killed", "timeout"}


def main() -> int:
    floor = float(sys.argv[1])
    killed: Counter[str] = Counter()
    total: Counter[str] = Counter()
    for line in sys.stdin:
        name, _, status = line.strip().rpartition(": ")
        match = MUTANT.match(name)
        if match is None:
            continue
        if status == "not checked":
            print(f"{name} was not checked; did mutmut run finish?")
            return 1
        module = match["module"]
        total[module] += 1
        if status in KILLED:
            killed[module] += 1
    if not total:
        print("No mutants found; run mutmut first")
        return 1
    failed = False
    for module in sorted(total):
        score = 100 * killed[module] / total[module]
        verdict = "ok  " if score >= floor else "FAIL"
        print(f"{verdict} {module}: {killed[module]}/{total[module]} killed ({score:.1f}%)")
        failed = failed or score < floor
    if failed:
        print(f"Below the {floor:g}% floor: add a test for each meaningful surviving mutant")
        print("(uv run mutmut results, then uv run mutmut show NAME)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
