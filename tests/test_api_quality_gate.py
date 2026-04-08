from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_api_config_from_analysis import build_api_config
from scripts.api_quality_gate import enforce_quality_gate, evaluate_api_quality


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_quality_gate_reports_generic_fallbacks_for_show_fixture() -> None:
    report = evaluate_api_quality(
        _load_json(FIXTURE_ROOT / "expected_api_config_show.json"),
        _load_json(FIXTURE_ROOT / "analysis_show.json"),
    )

    assert report["status"] == "review"
    assert report["warnings"] >= 1
    issue_codes = {issue["code"] for issue in report["issues"]}
    assert "generic_param_description" in issue_codes
    assert "generic_overview_summary" in issue_codes


def test_quality_gate_strict_blocks_unresolved_text() -> None:
    report = evaluate_api_quality(
        _load_json(FIXTURE_ROOT / "expected_api_config_to_confirm.json"),
        _load_json(FIXTURE_ROOT / "analysis_to_confirm.json"),
    )

    assert report["errors"] >= 1
    with pytest.raises(ValueError, match="API quality gate failed"):
        enforce_quality_gate(report, mode="strict")


def test_quality_gate_report_mode_does_not_raise() -> None:
    report = evaluate_api_quality(
        _load_json(FIXTURE_ROOT / "expected_api_config_to_confirm.json"),
        _load_json(FIXTURE_ROOT / "analysis_to_confirm.json"),
    )

    warnings = enforce_quality_gate(report, mode="report")
    assert isinstance(warnings, list)


def test_quality_gate_accepts_specialized_gaibudatatorikomi_show_contract() -> None:
    analysis_payload = {
        "feature": "/api/gaiBuDataTorikomi/show",
        "request_params": [{"name": "baseDate"}, {"name": "functionId"}, {"name": "page"}],
        "response_params": [{"name": "kinoPermissionMap"}, {"name": "condition"}, {"name": "pageSize"}],
    }
    project_config = _load_json(Path(__file__).resolve().parents[1] / "configs" / "project_config.json")
    config = build_api_config(project_config=project_config, analysis_payload=analysis_payload)

    report = evaluate_api_quality(config, analysis_payload)

    assert report["errors"] == 0
