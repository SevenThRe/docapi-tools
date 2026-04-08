from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"
BACK_ROOT = FIXTURE_ROOT / "back" / "src" / "main" / "java"
CONTROLLER_DIR = BACK_ROOT / "jp" / "co" / "fminc" / "socia" / "aplAprList"


def _cli_command() -> list[str]:
    return [sys.executable, "-m", "scripts.docapi_cli"]


def _run_docapi(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_cli_command(), *args],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_docapi_generate_writes_phase1_artifacts(tmp_path: Path) -> None:
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

    run_dirs = sorted(path for path in tmp_path.iterdir() if path.is_dir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]

    scan_path = run_dir / "scan.json"
    analysis_path = run_dir / "analysis.json"
    api_config_path = run_dir / "api_config.json"
    quality_path = run_dir / "quality_report.json"
    workbook_path = run_dir / "api_spec.xlsx"
    export_path = run_dir / "export.json"
    workbook_validation_path = run_dir / "workbook_validation.json"
    manifest_path = run_dir / "manifest.json"
    published_workbooks = sorted(path for path in tmp_path.iterdir() if path.is_file() and path.suffix == ".xlsx")

    assert scan_path.exists()
    assert analysis_path.exists()
    assert api_config_path.exists()
    assert quality_path.exists()
    assert workbook_path.exists()
    assert export_path.exists()
    assert workbook_validation_path.exists()
    assert manifest_path.exists()
    assert len(published_workbooks) == 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    api_config = json.loads(api_config_path.read_text(encoding="utf-8"))
    quality_report = json.loads(quality_path.read_text(encoding="utf-8"))
    export_report = json.loads(export_path.read_text(encoding="utf-8"))
    workbook_validation = json.loads(workbook_validation_path.read_text(encoding="utf-8"))

    assert manifest["artifacts"]["scan"] == "scan.json"
    assert manifest["artifacts"]["analysis"] == "analysis.json"
    assert manifest["artifacts"]["api_config"] == "api_config.json"
    assert manifest["artifacts"]["quality_report"] == "quality_report.json"
    assert manifest["artifacts"]["api_spec"] == "api_spec.xlsx"
    assert manifest["artifacts"]["export"] == "export.json"
    assert manifest["artifacts"]["workbook_validation"] == "workbook_validation.json"
    assert manifest["artifacts"]["published_api_spec"] == published_workbooks[0].name
    assert {"cover", "api_info", "request_params", "response_params"} <= set(api_config)
    assert api_config["api_info"]["url"] == "/api/aplAprList/show"
    assert {"status", "score", "issues"} <= set(quality_report)
    assert {"output_path", "template_path", "project_config_path", "published_output_path"} <= set(export_report)
    assert {"status", "issues", "sheet_names"} <= set(workbook_validation)
    assert Path(export_report["published_output_path"]).name == published_workbooks[0].name
