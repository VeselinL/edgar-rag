"""Run pytest and expose failure details as GitHub check annotations."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ElementTree


def annotation(value: str) -> str:
    return (
        value.replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def main() -> None:
    arguments = sys.argv[1:]
    if arguments[:1] == ["--"]:
        arguments = arguments[1:]
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "pytest.xml"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", *arguments, f"--junitxml={report}"],
            check=False,
        )
        if result.returncode and report.is_file():
            root = ElementTree.parse(report).getroot()
            for case in root.iter("testcase"):
                failure = case.find("failure")
                if failure is None:
                    failure = case.find("error")
                if failure is None:
                    continue
                name = f"{case.get('classname', '')}.{case.get('name', '')}"
                details = (failure.text or failure.get("message") or "pytest failed")[-3000:]
                print(f"::error title={annotation(name)}::{annotation(details)}")
        raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
