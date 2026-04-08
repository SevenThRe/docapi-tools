from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.build_api_config_from_analysis import build_api_config


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"
PROJECT_CONFIG_PATH = PACKAGE_ROOT / "configs" / "project_config.json"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_build_api_config_matches_show_fixture() -> None:
    analysis_payload = _load_json(FIXTURE_ROOT / "analysis_show.json")
    project_config = _load_json(PROJECT_CONFIG_PATH)

    config = build_api_config(
        analysis_payload,
        project_config,
        overrides={
            "cover": {
                "create_date": "2026-04-07",
                "update_date": "2026-04-07",
            }
        },
    )

    assert config == _load_json(FIXTURE_ROOT / "expected_api_config_show.json")
    assert config["api_info"]["url"] == "/api/aplAprList/show"
    assert config["request_params"][0]["item_name"] == "functionId"
    assert config["response_params"][0]["data_type"] == "Array"


def test_build_api_config_preserves_explicit_to_confirm_text() -> None:
    analysis_payload = _load_json(FIXTURE_ROOT / "analysis_to_confirm.json")
    project_config = _load_json(PROJECT_CONFIG_PATH)

    config = build_api_config(
        analysis_payload,
        project_config,
        overrides={
            "cover": {
                "create_date": "2026-04-07",
                "update_date": "2026-04-07",
            }
        },
    )

    assert config == _load_json(FIXTURE_ROOT / "expected_api_config_to_confirm.json")
    assert "to confirm" in config["cover"]["api_name"]
    assert "warning" not in config["api_info"]["description"].lower()


def test_builder_cli_writes_expected_sections(tmp_path: Path) -> None:
    output_path = tmp_path / "api_config.json"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/build_api_config_from_analysis.py",
            str(FIXTURE_ROOT / "analysis_show.json"),
            "-p",
            str(PROJECT_CONFIG_PATH),
            "-o",
            str(output_path),
        ],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = _load_json(output_path)
    assert {"cover", "api_info", "request_params", "response_params"} <= set(config)
    assert config["api_info"]["method"] == "POST"


def test_build_api_config_specializes_gaibudatatorikomi_show() -> None:
    project_config = _load_json(PROJECT_CONFIG_PATH)
    analysis_payload = {
        "feature": "/api/gaiBuDataTorikomi/show",
        "request_params": [{"name": "baseDate"}, {"name": "functionId"}, {"name": "page"}],
        "response_params": [{"name": "kinoPermissionMap"}, {"name": "pageSize"}, {"name": "condition"}],
    }

    config = build_api_config(
        analysis_payload,
        project_config,
        overrides={
            "cover": {
                "create_date": "2026-04-08",
                "update_date": "2026-04-08",
            }
        },
    )

    assert config["cover"]["api_name"] == "外部データ取込画面を表示する"
    assert config["cover"]["feature_name"] == "外部データ取込"
    assert config["cover"]["operation_name"] == "表示"
    assert [param["param_name"] for param in config["request_params"]] == ["functionId"]
    assert config["request_params"][0]["item_name"] == "機能ID"
    assert config["request_params"][0]["required"] == "○"
    assert [param["param_name"] for param in config["response_params"]] == ["data"]
    assert config["response_params"][0]["item_name"] == "レスポンスデータ"
    data = config["response_params"][0]
    assert data["required"] == "○"
    assert data["children"][0]["param_name"] == "kinoPermissionMap"
    assert data["children"][0]["required"] == "○"
    assert data["children"][0]["item_name"] == "機能権限マップ"
    optional_names = {
        child["param_name"]
        for child in data["children"]
        if child["required"] == "△"
    }
    assert optional_names == {
        "taikeiList",
        "taikeiItemList",
        "kyuyoPaymentNengetsuList",
        "rinjiPaymentNengetsuList",
        "syoyoPaymentNengetsuList",
        "condition",
    }
    narrow_range = next(child for child in data["children"] if child["param_name"] == "filePropertyNarrowRange")
    assert [child["param_name"] for child in narrow_range["children"]] == [
        "updateCondition",
        "duplicateOrder",
        "optionMatch",
        "errorCheck",
        "dataEndType",
    ]
    assert [child["param_name"] for child in narrow_range["children"][0]["children"]] == ["TEXT", "EMPTY"]
    defaults = next(child for child in data["children"] if child["param_name"] == "filePropertyDefaults")
    assert defaults["children"][0]["param_name"] == "fileCharset"
    assert defaults["children"][2]["param_name"] == "dataEndType"
    assert defaults["children"][3]["param_name"] == "dataRowEndNumber"
    assert defaults["children"][-1]["param_name"] == "normalUpdate"
    detail = config["processing_detail"]["steps"][0]
    assert detail["title"] == "初期表示リクエストを受信する"
    assert config["processing_detail"]["steps"][1]["title"] == "共通コンテキストを補完する"
    branch_step = config["processing_detail"]["steps"][2]
    assert branch_step["title"] == "functionId 別の初期データを取得する"
    assert [child["title"] for child in branch_step["children"]] == [
        "gaibuDataTorikomiPayment の場合",
        "gaibuDataTorikomiSyugyoKinmu の場合",
        "gaibuDataTorikomiSyainKoumoku の場合",
    ]
    assert branch_step["children"][0]["content"][0] == "体系列表を取得する。"
    assert branch_step["children"][0]["content"][2] == "体系項目一覧を取得する。"
    assert branch_step["children"][0]["content"][4] == "給与支給年月一覧、臨時給与支給年月一覧、賞与支給年月一覧を取得する。"
    assert branch_step["children"][1]["content"][0] == "就業体系列表を取得する。"
    assert branch_step["children"][0]["content"][1]["type"] == "mybatis_sql"
