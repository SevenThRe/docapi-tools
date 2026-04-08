from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.export_api_spec import export_api_workbook


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _analysis_body(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("analysis")
    if isinstance(nested, dict):
        return nested
    return payload


def _param_row(name: str, *, is_response: bool) -> dict[str, Any]:
    description = f"{name}を返却する。" if is_response else f"{name}を指定する。"
    return {
        "item_name": name,
        "required": "△",
        "data_type": "Array" if name.endswith("List") else "String",
        "data_length": "-",
        "description": description,
        "example": "",
        "depth": 0,
    }


def repair_api_run(run_dir: str | Path, *, findings_path: str | Path | None = None) -> dict[str, Any]:
    run_path = Path(run_dir).expanduser().resolve()
    api_config_path = run_path / "api_config.json"
    analysis_path = run_path / "analysis.json"
    default_findings_path = run_path / "review_findings.json"
    findings_file = Path(findings_path).expanduser().resolve() if findings_path else default_findings_path

    if not api_config_path.exists():
        raise ValueError(f"api_config.json not found in run dir: {run_path}")
    if not findings_file.exists():
        raise ValueError(f"review findings not found: {findings_file}")

    api_config = json.loads(api_config_path.read_text(encoding="utf-8"))
    analysis_payload = json.loads(analysis_path.read_text(encoding="utf-8")) if analysis_path.exists() else {}
    findings_payload = json.loads(findings_file.read_text(encoding="utf-8"))
    findings = findings_payload.get("findings", [])
    analysis_body = _analysis_body(analysis_payload)

    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    request_names = {
        str(param.get("name") or param.get("item_name") or "").strip()
        for param in analysis_body.get("request_params", [])
        if isinstance(param, dict)
    }
    response_names = {
        str(param.get("name") or param.get("item_name") or "").strip()
        for param in analysis_body.get("response_params", [])
        if isinstance(param, dict)
    }

    if any(item.get("code") == "analysis_param_missing" for item in findings):
        config_request_names = {row.get("item_name", "") for row in api_config.get("request_params", []) if isinstance(row, dict)}
        config_response_names = {row.get("item_name", "") for row in api_config.get("response_params", []) if isinstance(row, dict)}
        for name in sorted(request_names - config_request_names):
            api_config.setdefault("request_params", []).append(_param_row(name, is_response=False))
            applied.append({"code": "analysis_param_missing", "target": f"request_params.{name}"})
        for name in sorted(response_names - config_response_names):
            api_config.setdefault("response_params", []).append(_param_row(name, is_response=True))
            applied.append({"code": "analysis_param_missing", "target": f"response_params.{name}"})

    for collection_name, is_response in (("request_params", False), ("response_params", True)):
        for row in api_config.get(collection_name, []):
            if not isinstance(row, dict):
                continue
            description = str(row.get("description") or "").strip()
            if collection_name == "request_params" and description.startswith("リクエストパラメーター（"):
                row["description"] = f"{row['item_name']}を指定する。"
                applied.append({"code": "generic_param_description", "target": f"{collection_name}.{row['item_name']}"})
            if collection_name == "response_params" and description.startswith("レスポンス項目（"):
                row["description"] = f"{row['item_name']}を返却する。"
                applied.append({"code": "generic_param_description", "target": f"{collection_name}.{row['item_name']}"})

    api_info = api_config.setdefault("api_info", {})
    cover = api_config.setdefault("cover", {})
    overview = api_config.setdefault("overview", {})
    sequence = api_config.setdefault("sequence", {})
    processing_detail = api_config.setdefault("processing_detail", {})

    description = str(api_info.get("description") or cover.get("api_name") or "API処理").strip()
    feature_name = str(cover.get("feature_name") or cover.get("api_name") or "API").strip()

    if str(overview.get("summary") or "").endswith("ためのAPI。"):
        overview["summary"] = f"{description}を行うAPI。"
        applied.append({"code": "generic_overview_summary", "target": "overview.summary"})

    labels = [step.get("label") for step in overview.get("flow_steps", []) if isinstance(step, dict)]
    generic_labels = {"開始", "リクエスト受信", "パラメーター確認", "内部処理実行", "レスポンス返却", "終了"}
    if labels and set(labels) <= generic_labels:
        overview["flow_steps"] = [
            {"label": "開始", "type": "terminal"},
            {"label": f"{feature_name}リクエスト受付", "type": "process"},
            {"label": "入力内容確認", "type": "process"},
            {"label": "業務処理実行", "type": "process"},
            {"label": "レスポンス返却", "type": "process"},
            {"label": "終了", "type": "terminal"},
        ]
        applied.append({"code": "generic_overview_flow", "target": "overview.flow_steps"})

    if str(sequence.get("client_component") or "").strip() == "呼出元画面":
        sequence["client_component"] = f"{feature_name}画面"
        applied.append({"code": "generic_client_component", "target": "sequence.client_component"})

    for step in sequence.get("steps", []):
        if not isinstance(step, dict):
            continue
        desc = str(step.get("description") or "").strip()
        if desc == "入力された条件を受け取り、内部処理を開始する。":
            step["description"] = f"{description}に必要な入力条件を受け取り、業務処理を開始する。"
            applied.append({"code": "generic_sequence_description", "target": "sequence.steps.request"})
        elif desc == "処理結果を呼出元へ返却する。":
            step["description"] = f"{description}の結果を呼出元へ返却する。"
            applied.append({"code": "generic_sequence_description", "target": "sequence.steps.response"})

    for step in processing_detail.get("steps", []):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if title == "リクエスト受付":
            step["title"] = f"{feature_name}リクエスト受付"
            applied.append({"code": "generic_processing_title", "target": "processing_detail.steps.request"})
        elif title == "レスポンス生成":
            step["title"] = f"{feature_name}レスポンス生成"
            applied.append({"code": "generic_processing_title", "target": "processing_detail.steps.response"})

        new_content: list[Any] = []
        for content in step.get("content", []):
            if content == "取得結果をAPIレスポンス形式へ整形する。":
                new_content.append(f"{description}の取得結果をAPIレスポンス形式へ整形する。")
                applied.append({"code": "generic_processing_content", "target": step["title"]})
            else:
                new_content.append(content)
        step["content"] = new_content

    backup_path = run_path / "api_config.before_repair.json"
    shutil.copyfile(api_config_path, backup_path)
    api_config_path.write_text(json.dumps(api_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    repaired_workbook = run_path / "api_spec.repaired.xlsx"
    export_meta = export_api_workbook(str(api_config_path), output_path=str(repaired_workbook))

    report = {
        "generated_at": _timestamp(),
        "run_dir": str(run_path),
        "backup_config": str(backup_path),
        "updated_config": str(api_config_path),
        "repaired_workbook": str(repaired_workbook),
        "applied_repairs": applied,
        "skipped_findings": skipped,
        "export_meta": export_meta,
    }
    return report
