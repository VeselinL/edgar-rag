"""Fail a CI scan while surfacing SARIF findings as check annotations."""

from __future__ import annotations

import json
from pathlib import Path
import sys


def escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def main() -> None:
    path = Path(sys.argv[1])
    report = json.loads(path.read_text(encoding="utf-8"))
    findings = [
        result
        for run in report.get("runs", [])
        for result in run.get("results", [])
    ]
    for result in findings[:50]:
        rule = str(result.get("ruleId", "security finding"))
        message = str(result.get("message", {}).get("text", "Container scan failed."))
        print(f"::error title={escape(rule)}::{escape(message)}")
    if findings:
        print(f"Container scan reported {len(findings)} blocking finding(s).")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
