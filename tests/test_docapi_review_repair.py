from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"
BACK_ROOT = FIXTURE_ROOT / "back" / "src" / "main" / "java"
CONTROLLER_DIR = BACK_ROOT / "jp" / "co" / "fminc" / "socia" / "aplAprList"


def _run_docapi(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "scripts.docapi_cli", *args],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _make_run(tmp_path: Path) -> Path:
    result = _run_docapi(
        "generate",
        "--path",
        str(CONTROLLER_DIR),
        "--pick",
        "1",
        "--yes",
        "--non-interactive",
        "--output-dir",
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr
    return next(path for path in tmp_path.iterdir() if path.is_dir())


def test_docapi_review_writes_machine_readable_findings(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    result = _run_docapi("review", "--run-dir", str(run_dir))
    assert result.returncode == 0, result.stderr

    findings_path = run_dir / "review_findings.json"
    assert findings_path.exists()
    findings = json.loads(findings_path.read_text(encoding="utf-8"))
    assert {"status", "summary", "findings"} <= set(findings)
    assert findings["summary"]["findings"] >= 1


def test_docapi_repair_updates_config_and_regenerates_workbook(tmp_path: Path) -> None:
    run_dir = _make_run(tmp_path)
    review_result = _run_docapi("review", "--run-dir", str(run_dir))
    assert review_result.returncode == 0, review_result.stderr

    repair_result = _run_docapi("repair", "--run-dir", str(run_dir))
    assert repair_result.returncode == 0, repair_result.stderr

    backup_path = run_dir / "api_config.before_repair.json"
    repair_report_path = run_dir / "repair_report.json"
    repaired_workbook_path = run_dir / "api_spec.repaired.xlsx"
    manifest_path = run_dir / "manifest.json"

    assert backup_path.exists()
    assert repair_report_path.exists()
    assert repaired_workbook_path.exists()

    repair_report = json.loads(repair_report_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert repair_report["applied_repairs"]
    assert manifest["artifacts"]["review_findings"] == "review_findings.json"
    assert manifest["artifacts"]["repair_report"] == "repair_report.json"
    assert manifest["artifacts"]["api_spec_repaired"] == "api_spec.repaired.xlsx"
