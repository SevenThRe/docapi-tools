from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.docapi_cli import package_to_relative_path
from scripts.extract_api_inventory import build_scan_artifact


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"
BACK_ROOT = FIXTURE_ROOT / "back" / "src" / "main" / "java"
PACKAGE_ROOT = BACK_ROOT / "jp" / "co" / "fminc" / "socia" / "aplAprList"


def _cli_command() -> list[str]:
    return [sys.executable, "-m", "scripts.docapi_cli"]


def test_package_target_normalization() -> None:
    assert package_to_relative_path("jp.co.fminc.socia.aplAprList").as_posix() == "jp/co/fminc/socia/aplAprList"


def test_build_scan_artifact_dedupe_and_confidence(tmp_path: Path) -> None:
    artifact = build_scan_artifact(
        target_mode="package",
        target_value="jp.co.fminc.socia.aplAprList",
        resolved_target="jp/co/fminc/socia/aplAprList",
        back_root=str(BACK_ROOT),
        front_root=None,
        output_json=str(tmp_path / "scan.json"),
    )

    dedupe_keys = [candidate["dedupe_key"] for candidate in artifact["candidates"]]
    assert dedupe_keys.count("POST /api/aplAprList/show") == 1
    assert "GET /api/aplAprList/status" in dedupe_keys

    confidence_by_controller = {
        candidate["controller_class"]: candidate["confidence"] for candidate in artifact["candidates"]
    }
    assert confidence_by_controller["ConstantPathController"] == "LOW"

    warnings = artifact["warnings"]
    assert any(warning["code"] == "duplicate_endpoint" for warning in warnings)
    assert any(warning["code"] == "non_literal_mapping" for warning in warnings)


def test_path_target_infers_back_root(tmp_path: Path) -> None:
    artifact = build_scan_artifact(
        target_mode="path",
        target_value=str(PACKAGE_ROOT),
        resolved_target=str(PACKAGE_ROOT),
        back_root=None,
        front_root=None,
        output_json=str(tmp_path / "scan.json"),
    )

    assert artifact["scan"]["roots"]["back_root"] == str(BACK_ROOT.resolve())
    assert len(artifact["candidates"]) >= 2


def test_scan_cli(tmp_path: Path) -> None:
    output_json = tmp_path / "scan.json"
    result = subprocess.run(
        [
            *_cli_command(),
            "scan",
            "--package",
            "jp.co.fminc.socia.aplAprList",
            "--back-root",
            str(BACK_ROOT),
            "--output-json",
            str(output_json),
            "--non-interactive",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "index" in result.stdout
    assert "/api/aplAprList/show" in result.stdout
    assert "AplAprListController.show" in result.stdout

    scan_payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert set(scan_payload.keys()) == {"tool", "inputs", "roots", "dedupe", "candidates", "warnings"}
    assert scan_payload["candidates"][0]["dedupe_key"]
    assert scan_payload["warnings"]
