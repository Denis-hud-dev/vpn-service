"""Render a short GitHub Actions summary from pytest JUnit XML without tracebacks."""

from pathlib import Path
import sys
from xml.etree import ElementTree


def render(path: Path) -> str:
    if not path.is_file():
        return "## KudaVPN tests\n\nPytest did not produce a report. Check the preceding CI steps.\n"
    root = ElementTree.parse(path).getroot()
    cases = root.findall(".//testcase")
    failed = [case for case in cases if case.find("failure") is not None or case.find("error") is not None]
    skipped = sum(case.find("skipped") is not None for case in cases)
    passed = len(cases) - len(failed) - skipped
    lines = ["## KudaVPN tests", "", f"Passed: {passed} · Failed: {len(failed)} · Skipped: {skipped}"]
    if failed:
        lines.extend(["", "Failed cases:"])
        lines.extend(f"- `{case.get('classname', '')}.{case.get('name', '')}`" for case in failed)
    if skipped:
        lines.extend(["", "Review skipped tests to confirm expected database coverage."])
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    report = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("reports/pytest.xml")
    sys.stdout.write(render(report))
