from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.api_quality_gate import evaluate_api_quality
from scripts.validate_api_workbook import validate_api_workbook


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def review_api_run(run_dir: str | Path) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    api_config_path = run_path / "api_config.json"
    analysis_path = run_path / "analysis.json"
    workbook_path = run_path / "api_spec.xlsx"

    if not api_config_path.exists():
        raise ValueError(f"api_config.json not found in run dir: {run_path}")

    api_config = json.loads(api_config_path.read_text(encoding="utf-8"))
    analysis_payload = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else {}
    quality_report = evaluate_api_quality(api_config, analysis_payload)
    workbook_report = (
        validate_api_workbook(workbook_path, api_config=api_config, analysis_payload=analysis_payload)
        if workbook_path.exists()
        else {"status": "skipped", "issues": [], "workbook_path": str(workbook_path)}
    )

    findings: list[dict[str, Any]] = []
    for issue in quality_report.get("issues", []):
        findings.append(
            {
                "source": "api_quality_gate",
                "severity": issue["severity"],
                "code": issue["code"],
                "message": issue["message"],
                "path": issue.get("path"),
                "repairable": issue["code"] in {
                    "analysis_param_missing",
                    "generic_param_description",
                    "generic_overview_summary",
                    "generic_overview_flow",
                    "generic_client_component",
                    "generic_sequence_description",
                    "generic_processing_title",
                    "generic_processing_content",
                },
            }
        )
    for issue in workbook_report.get("issues", []):
        findings.append(
            {
                "source": "api_workbook_validator",
                "severity": issue["severity"],
                "code": issue["code"],
                "message": issue["message"],
                "path": issue.get("sheet"),
                "repairable": False,
            }
        )

    error_count = sum(1 for finding in findings if finding["severity"] == "error")
    warning_count = sum(1 for finding in findings if finding["severity"] == "warning")
    status = "pass"
    if error_count:
        status = "fail"
    elif warning_count:
        status = "review"

    report = {
        "generated_at": _timestamp(),
        "run_dir": str(run_path),
        "status": status,
        "summary": {
            "errors": error_count,
            "warnings": warning_count,
            "findings": len(findings),
        },
        "sources": {
            "api_config": str(api_config_path),
            "analysis": str(analysis_path) if analysis_path.exists() else None,
            "workbook": str(workbook_path) if workbook_path.exists() else None,
        },
        "quality_report": quality_report,
        "workbook_report": workbook_report,
        "findings": findings,
    }
    return report
