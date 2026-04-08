from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"
BACK_PROJECT_ROOT = FIXTURE_ROOT / "back"
BACK_ROOT = BACK_PROJECT_ROOT / "src" / "main" / "java"
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


def _run_directories(output_dir: Path) -> list[Path]:
    return sorted(path for path in output_dir.iterdir() if path.is_dir())


def test_generate_selection_writes_manifest_and_scan(tmp_path: Path) -> None:
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
    run_dirs = _run_directories(tmp_path)
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert run_dir.name.startswith("20")
    assert (run_dir / "manifest.json").exists()
    assert (run_dir / "scan.json").exists()

    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selected_api"]["path"] == "/api/aplAprList/show"
    assert manifest["selected_index"] == 1
    assert manifest["artifacts"]["scan"] == "scan.json"
    assert manifest["artifacts"]["analysis"] == "analysis.json"


def test_generate_selection_supports_comma_separated_picks(tmp_path: Path) -> None:
    result = _run_docapi(
        "generate",
        "--path",
        str(CONTROLLER_DIR),
        "--pick",
        "1,2",
        "--yes",
        "--non-interactive",
        "--output-dir",
        str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    run_dirs = _run_directories(tmp_path)
    assert len(run_dirs) == 2

    selected_indexes = {
        json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["selected_index"]
        for run_dir in run_dirs
    }
    assert selected_indexes == {1, 2}


def test_generate_selection_requires_yes_for_single_api_confirmation(tmp_path: Path) -> None:
    result = _run_docapi(
        "generate",
        "--api",
        "/api/aplAprList/show",
        "--back-root",
        str(BACK_ROOT),
        "--non-interactive",
        "--output-dir",
        str(tmp_path),
    )

    assert result.returncode != 0
    assert "--yes" in result.stderr


def test_generate_analysis_artifacts_include_backend_evidence_paths(tmp_path: Path) -> None:
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
    run_dir = _run_directories(tmp_path)[0]
    analysis_payload = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["artifacts"]["analysis"] == "analysis.json"
    assert analysis_payload["selected_api"]["path"] == "/api/aplAprList/show"
    assert analysis_payload["evidence_roots"]["back_root"] == str(BACK_ROOT.resolve())
    assert analysis_payload["evidence_roots"]["project"] == str(BACK_PROJECT_ROOT.resolve())

    analysis = analysis_payload["analysis"]
    assert analysis["discovered_files"]["controllers"]
    assert analysis["discovered_files"]["services"]
    assert analysis["discovered_files"]["mappers"]
    assert analysis["discovered_files"]["mybatis_xml"]
    assert any(path.endswith("AplAprListController.java") for path in analysis["discovered_files"]["controllers"])
    assert any(path.endswith("AplAprListService.java") for path in analysis["discovered_files"]["services"])
    assert any(path.endswith("AplAprListMapper.java") for path in analysis["discovered_files"]["mappers"])
    assert any(path.endswith("AplAprListMapper.xml") for path in analysis["discovered_files"]["mybatis_xml"])
    assert analysis["request_params"]
    assert analysis["response_params"]
