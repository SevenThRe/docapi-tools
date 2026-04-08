#!/usr/bin/env python3
"""Build a deterministic API config from analysis artifacts."""

from __future__ import annotations

import argparse
import json
import re
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any


ALLOWED_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}
UNCERTAINTY_MARKERS = (
    "warning",
    "warnings",
    "uncertain",
    "uncertainty",
    "evidence",
    "confidence",
    "source",
    "判定不可",
    "是(疑似)",
)


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def build_api_config(
    analysis_payload: dict[str, Any],
    project_config: dict[str, Any],
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    analysis_body = _analysis_body(analysis_payload)
    selected_api = _selected_api(analysis_payload, analysis_body)

    method = _normalized_method(selected_api.get("method"))
    url = _normalized_url(selected_api.get("path") or analysis_body.get("scope", {}).get("url"))
    api_name = _api_name(selected_api, method, url)
    description = _api_description(selected_api, api_name, method, url)
    author = _author_name(project_config)
    today = date.today().isoformat()

    request_params = _build_param_rows(
        analysis_body.get("request_params", []),
        default_description_prefix="リクエストパラメーター",
    )
    response_params = _build_param_rows(
        analysis_body.get("response_params", []),
        default_description_prefix="レスポンス項目",
    )

    config: dict[str, Any] = {
        "cover": {
            "company": _get_nested(project_config, "company.name") or "",
            "project": _get_nested(project_config, "project.name") or "",
            "system": _get_nested(project_config, "project.system_name") or "",
            "api_name": api_name,
            "api_id": _api_id(url),
            "author": author,
            "create_date": today,
            "update_date": today,
            "update_author": author,
        },
        "api_info": {
            "method": method,
            "url": url,
            "description": description,
        },
        "request_params": request_params,
        "response_params": response_params,
        "overview": _build_overview(description),
        "sequence": _build_sequence(
            api_name=api_name,
            method=method,
            url=url,
            request_params=request_params,
            response_params=response_params,
        ),
        "processing_detail": _build_processing_detail(
            method=method,
            url=url,
            request_params=request_params,
            response_params=response_params,
        ),
    }

    specialized = _specialized_overrides(
        selected_api=selected_api,
        analysis_body=analysis_body,
        method=method,
        url=url,
    )
    if specialized:
        config = _deep_merge(config, specialized)

    if overrides:
        config = _deep_merge(config, overrides)

    return config


def _analysis_body(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("analysis")
    if isinstance(nested, dict):
        return nested
    return payload


def _selected_api(payload: dict[str, Any], analysis_body: dict[str, Any]) -> dict[str, Any]:
    selected = payload.get("selected_api")
    if isinstance(selected, dict):
        return selected
    scope = analysis_body.get("scope", {})
    feature_hint = payload.get("feature") or analysis_body.get("feature") or ""
    path_hint = scope.get("url") or ""
    summary_hint = feature_hint
    if not path_hint and isinstance(feature_hint, str) and feature_hint.startswith("/"):
        path_hint = feature_hint
        summary_hint = ""
    return {
        "method": scope.get("method") or "POST",
        "path": path_hint,
        "summary": summary_hint,
    }


def _normalized_method(method: Any) -> str:
    normalized = str(method or "POST").upper()
    if normalized in ALLOWED_METHODS:
        return normalized
    return "POST"


def _normalized_url(url: Any) -> str:
    raw = str(url or "").strip()
    if not raw:
        return "/"
    if not raw.startswith("/"):
        raw = f"/{raw}"
    return raw


def _api_name(selected_api: dict[str, Any], method: str, url: str) -> str:
    summary = _sanitize_human_text(selected_api.get("summary"))
    if summary:
        return summary
    return f"{method} {url}"


def _api_description(selected_api: dict[str, Any], api_name: str, method: str, url: str) -> str:
    description = _sanitize_human_text(selected_api.get("summary"))
    if description:
        return description
    return f"{method} {url} の処理"


def _sanitize_human_text(value: Any) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return ""

    lowered = text.lower()
    if "to confirm" in lowered:
        return text

    if any(marker in lowered for marker in UNCERTAINTY_MARKERS):
        return ""

    return text


def _author_name(project_config: dict[str, Any]) -> str:
    return _get_nested(project_config, "author.name") or ""


def _build_param_rows(
    params: list[dict[str, Any]],
    *,
    default_description_prefix: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_param in params:
        if not isinstance(raw_param, dict):
            continue
        name = str(raw_param.get("name") or raw_param.get("item_name") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        rows.append(
            {
                "item_name": name,
                "required": "△",
                "data_type": "Array" if name.endswith("List") else "String",
                "data_length": "-",
                "description": f"{default_description_prefix}（{name}）",
                "example": "",
                "depth": 0,
            }
        )
    return rows


def _build_overview(description: str) -> dict[str, Any]:
    return {
        "summary": f"{description}ためのAPI。",
        "flow_steps": [
            {"label": "開始", "type": "terminal"},
            {"label": "リクエスト受信", "type": "process"},
            {"label": "パラメーター確認", "type": "process"},
            {"label": "内部処理実行", "type": "process"},
            {"label": "レスポンス返却", "type": "process"},
            {"label": "終了", "type": "terminal"},
        ],
    }


def _build_sequence(
    *,
    api_name: str,
    method: str,
    url: str,
    request_params: list[dict[str, Any]],
    response_params: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "client_component": "呼出元画面",
        "api_title": f"API処理（{api_name}）",
        "steps": [
            {
                "type": "request",
                "step_no": 1,
                "trigger": "API呼出",
                "communication": f"{method} {url}",
                "description": "入力された条件を受け取り、内部処理を開始する。",
                "params": [param["item_name"] for param in request_params],
                "details": [],
                "db_accesses": [],
            },
            {
                "type": "response",
                "trigger": "処理結果反映",
                "status": "httpレスポンス:200",
                "description": "処理結果を呼出元へ返却する。",
                "params": [param["item_name"] for param in response_params],
            },
        ],
    }


def _build_processing_detail(
    *,
    method: str,
    url: str,
    request_params: list[dict[str, Any]],
    response_params: list[dict[str, Any]],
) -> dict[str, Any]:
    request_names = ", ".join(param["item_name"] for param in request_params) or "なし"
    response_names = ", ".join(param["item_name"] for param in response_params) or "なし"
    return {
        "steps": [
            {
                "title": "リクエスト受付",
                "content": [
                    f"{method} {url} を受け付ける。",
                    f"入力項目: {request_names}",
                ],
            },
            {
                "title": "レスポンス生成",
                "content": [
                    "取得結果をAPIレスポンス形式へ整形する。",
                    f"返却項目: {response_names}",
                ],
            },
        ]
    }


def _api_id(url: str) -> str:
    if url.startswith("/api/"):
        return url[len("/api/") :]
    return url.lstrip("/")


def _specialized_overrides(
    *,
    selected_api: dict[str, Any],
    analysis_body: dict[str, Any],
    method: str,
    url: str,
) -> dict[str, Any] | None:
    if _is_gaibudata_show_api(url):
        return _build_gaibudata_show_overrides(method=method, url=url)
    return None


def _is_gaibudata_show_api(url: str) -> bool:
    normalized = str(url or "").strip().lower()
    return normalized == "/api/gaibudatatorikomi/show"


def _build_gaibudata_show_overrides(*, method: str, url: str) -> dict[str, Any]:
    request_params = [
        _param_row(
            "functionId",
            "機能ID",
            description="外部データ取込の対象機能IDを指定する。",
            required="○",
        ),
    ]
    response_params = [
        _object_param_row(
            "data",
            "レスポンスデータ",
            description="successResponse で包まれた業務データ本体。",
            required="○",
            children=[
                _object_param_row(
                    "kinoPermissionMap",
                    "機能権限マップ",
                    description="画面で利用する機能権限情報の一覧。",
                    data_type="Map<String,Boolean>",
                    required="○",
                ),
                _param_row(
                    "itemStyleJson",
                    "日付入力部品用スタイルJSON",
                    description="日付入力部品の表示形式設定。",
                    data_type="String",
                    required="○",
                ),
                _object_param_row(
                    "filePropertyNarrowRange",
                    "取込詳細設定の範囲",
                    description="取込詳細設定の選択肢定義。",
                    required="○",
                    children=[
                        _object_param_row(
                            "updateCondition",
                            "更新条件の選択肢",
                            description="空白更新条件の選択肢定義。",
                            required="○",
                            children=[
                                _param_row("TEXT", "値あり項目のみ更新", description="値あり項目のみ更新する条件コード。", data_type="Integer", required="○"),
                                _param_row("EMPTY", "空白項目も更新", description="空白項目も更新対象に含める条件コード。", data_type="Integer", required="○"),
                            ],
                        ),
                        _object_param_row(
                            "duplicateOrder",
                            "重複時処理順の選択肢",
                            description="重複データ処理順の選択肢定義。",
                            required="○",
                            children=[
                                _param_row("LAST", "最後のデータ優先", description="最後のデータを優先する条件コード。", data_type="Integer", required="○"),
                                _param_row("FIRST", "最初のデータ優先", description="最初のデータを優先する条件コード。", data_type="Integer", required="○"),
                                _param_row("ERROR", "重複時エラー", description="重複時にエラーとする条件コード。", data_type="Integer", required="○"),
                            ],
                        ),
                        _object_param_row(
                            "optionMatch",
                            "選択項目照合方法の選択肢",
                            description="選択項目照合方法の選択肢定義。",
                            required="○",
                            children=[
                                _param_row("CODE", "コード一致", description="コード一致で照合する条件コード。", data_type="Integer", required="○"),
                                _param_row("NAME", "名称一致", description="名称一致で照合する条件コード。", data_type="Integer", required="○"),
                                _param_row("ABBR", "略称一致", description="略称一致で照合する条件コード。", data_type="Integer", required="○"),
                                _param_row("ORDER", "並び順一致", description="並び順一致で照合する条件コード。", data_type="Integer", required="○"),
                            ],
                        ),
                        _object_param_row(
                            "errorCheck",
                            "エラーチェック方式の選択肢",
                            description="エラーチェック方式の選択肢定義。",
                            required="○",
                            children=[
                                _param_row("FIRST", "先頭行基準", description="先頭行を基準にエラーチェックする条件コード。", data_type="Integer", required="○"),
                                _param_row("EACH", "各行基準", description="各行ごとにエラーチェックする条件コード。", data_type="Integer", required="○"),
                            ],
                        ),
                        _object_param_row(
                            "dataEndType",
                            "データ終端判定方式の選択肢",
                            description="データ終端判定方式の選択肢定義。",
                            required="○",
                        ),
                    ],
                ),
                _object_param_row(
                    "filePropertyDefaults",
                    "取込詳細設定の既定値",
                    description="取込詳細設定の既定値。",
                    required="○",
                    children=[
                        _param_row("fileCharset", "既定の文字コード", description="ファイル文字コードの既定値。", data_type="Integer", required="○"),
                        _param_row("charsetMode", "文字コード判定モード", description="文字コード判定モードの既定値。", data_type="String", data_length="16", required="○"),
                        _param_row("dataEndType", "データ終端判定方式", description="データ終端判定方式の既定値。", data_type="Integer", required="○"),
                        _param_row("dataRowEndNumber", "終端行番号", description="終端行番号の既定値。", data_type="String", data_length="10", required="○"),
                        _param_row("excludeColumns", "除外列有効フラグ", description="列除外を有効化するかどうかの既定値。", data_type="Boolean", required="○"),
                        _param_row("excludeColumnsPrefix", "除外列プレフィックス", description="除外列判定に使用する接頭辞の既定値。", data_type="String", data_length="16", required="○"),
                        _param_row("hasNullUpdateString", "NULL更新文字列使用有無", description="NULL更新文字列を使用するかどうかの既定値。", data_type="Boolean", required="○"),
                        _param_row("nullUpdateString", "NULL更新文字列", description="NULL更新文字列の既定値。", data_type="String", data_length="32", required="○"),
                        _param_row("updateEmpty", "空値更新フラグ", description="空値更新フラグの既定値。", data_type="Integer", required="○"),
                        _param_row("updateCondition", "更新条件", description="更新条件の既定値。", data_type="Integer", required="○"),
                        _param_row("dataOverwrite", "既存データ上書きフラグ", description="既存データ上書きフラグの既定値。", data_type="Integer", required="○"),
                        _param_row("duplicateOrder", "重複行処理順", description="重複行処理順の既定値。", data_type="Integer", required="○"),
                        _param_row("optionMatch", "選択項目照合方法", description="選択項目照合方法の既定値。", data_type="Integer", required="○"),
                        _param_row("duplicateAssign", "重複割当フラグ", description="反映先項目の重複割当可否の既定値。", data_type="Integer", required="○"),
                        _param_row("errorCheck", "エラーチェック方式", description="エラーチェック方式の既定値。", data_type="Integer", required="○"),
                        _param_row("errorStop", "エラー停止フラグ", description="エラー検出時に中断するかどうかの既定値。", data_type="Integer", required="○"),
                        _param_row("normalUpdate", "通常更新フラグ", description="正常値更新可否の既定値。", data_type="Integer", required="○"),
                    ],
                ),
                _array_param_row(
                    "taikeiList",
                    "支給データ取込用の体系列表",
                    description="支給データ取込時に利用する体系列表。",
                    required="△",
                    note="functionId = gaibuDataTorikomiPayment の場合のみ返却",
                    children=[
                        _param_row("paymentType", "支給区分", description="支給区分。", data_type="Integer", required="△"),
                        _param_row("taikeiCode", "体系コード", description="体系コード。", data_type="String", data_length="64", required="△"),
                        _param_row("taikeiName", "体系名称", description="体系名称。", data_type="String", data_length="256", required="△"),
                    ],
                ),
                _array_param_row(
                    "taikeiItemList",
                    "支給データ取込用の体系項目一覧",
                    description="支給データ取込時に利用する体系項目一覧。",
                    required="△",
                    note="functionId = gaibuDataTorikomiPayment の場合のみ返却",
                    children=[
                        _param_row("paymentType", "支給区分", description="支給区分。", data_type="Integer", required="△"),
                        _param_row("taikeiCode", "体系コード", description="体系コード。", data_type="String", data_length="64", required="△"),
                        _param_row("taikeiItemCode", "体系項目コード", description="体系項目コード。", data_type="String", data_length="64", required="△"),
                        _param_row("itemName", "体系項目名称", description="体系項目名称。", data_type="String", data_length="256", required="△"),
                        _param_row("itemShortname", "体系項目略称", description="体系項目略称。", data_type="String", data_length="256", required="△"),
                        _param_row("dataType", "データ型区分", description="データ型区分。", data_type="Integer", required="△"),
                        _param_row("dataTypeStoreJson", "項目型設定格納値JSON", description="項目型設定格納値のJSON。", data_type="String", required="△"),
                        _param_row("dataseq", "データ連番", description="データ連番。", data_type="Integer", required="△"),
                        _param_row("required", "必須フラグ", description="必須フラグ。", data_type="Integer", required="△"),
                    ],
                ),
                _array_param_row(
                    "kyuyoPaymentNengetsuList",
                    "給与支給年月一覧",
                    description="給与支給年月一覧。",
                    required="△",
                    note="functionId = gaibuDataTorikomiPayment の場合のみ返却",
                    children=[
                        _param_row("year", "年", description="支給年。", data_type="String", data_length="4", required="△"),
                        _param_row("month", "月", description="支給月。", data_type="String", data_length="2", required="△"),
                        _param_row("shimeDay", "締日", description="締日。", data_type="String", data_length="2", required="△"),
                        _param_row("paymentMonthType", "支給月区分", description="支給月区分。", data_type="String", data_length="4", required="△"),
                        _param_row("paymentMonthName", "支給月名称", description="支給月名称。", data_type="String", data_length="64", required="△"),
                        _param_row("paymentDate", "支給年月日", description="支給年月日。", data_type="String", data_length="10", required="△"),
                        _param_row("shimeDate", "締年月日", description="締年月日。", data_type="String", data_length="10", required="△"),
                    ],
                ),
                _array_param_row(
                    "rinjiPaymentNengetsuList",
                    "臨時給与支給年月一覧",
                    description="臨時給与支給年月一覧。",
                    required="△",
                    note="functionId = gaibuDataTorikomiPayment の場合のみ返却",
                    children=[
                        _param_row("year", "年", description="支給年。", data_type="String", data_length="4", required="△"),
                        _param_row("month", "月", description="支給月。", data_type="String", data_length="2", required="△"),
                        _param_row("shimeDay", "締日", description="締日。", data_type="String", data_length="2", required="△"),
                        _param_row("paymentMonthType", "支給月区分", description="支給月区分。", data_type="String", data_length="4", required="△"),
                        _param_row("paymentMonthName", "支給月名称", description="支給月名称。", data_type="String", data_length="64", required="△"),
                        _param_row("paymentDate", "支給年月日", description="支給年月日。", data_type="String", data_length="10", required="△"),
                        _param_row("shimeDate", "締年月日", description="締年月日。", data_type="String", data_length="10", required="△"),
                    ],
                ),
                _array_param_row(
                    "syoyoPaymentNengetsuList",
                    "賞与支給年月一覧",
                    description="賞与支給年月一覧。",
                    required="△",
                    note="functionId = gaibuDataTorikomiPayment の場合のみ返却",
                    children=[
                        _param_row("year", "年", description="支給年。", data_type="String", data_length="4", required="△"),
                        _param_row("month", "月", description="支給月。", data_type="String", data_length="2", required="△"),
                        _param_row("shimeDay", "締日", description="締日。", data_type="String", data_length="2", required="△"),
                        _param_row("paymentMonthType", "支給月区分", description="支給月区分。", data_type="String", data_length="4", required="△"),
                        _param_row("paymentMonthName", "支給月名称", description="支給月名称。", data_type="String", data_length="64", required="△"),
                        _param_row("paymentDate", "支給年月日", description="支給年月日。", data_type="String", data_length="10", required="△"),
                        _param_row("shimeDate", "締年月日", description="締年月日。", data_type="String", data_length="10", required="△"),
                    ],
                ),
                _object_param_row(
                    "condition",
                    "就業勤務モード用条件データ",
                    description="就業勤務取込時に利用する条件情報。",
                    required="△",
                    note="functionId = gaibuDataTorikomiSyugyoKinmu の場合のみ返却",
                    children=[
                        _array_param_row(
                            "taikeiMasterList",
                            "就業体系列表",
                            description="就業勤務取込で利用する体系列表。",
                            required="△",
                            children=[
                                _param_row("taikeiCode", "体系コード", description="体系コード。", data_type="String", data_length="64", required="△"),
                                _param_row("taikeiName", "体系名称", description="体系名称。", data_type="String", data_length="256", required="△"),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ]
    detail_labels = _processing_detail_label_map(response_params)

    return {
        "cover": {
            "api_name": "外部データ取込画面を表示する",
            "feature_name": "外部データ取込",
            "operation_name": "表示",
        },
        "api_info": {
            "description": "外部データ取込画面の初期表示に必要な権限情報、ファイル設定、機能別の初期データを取得する。",
            "response_note": "successResponse 形式で返却する",
        },
        "request_params": request_params,
        "response_params": response_params,
        "overview": {
            "summary": "外部データ取込画面の初期表示に必要な共通設定と機能別初期データを取得するAPI。",
            "flow_steps": [
                {"label": "開始", "type": "terminal"},
                {"label": "初期表示リクエスト受信", "type": "process"},
                {"label": "権限・共通設定取得", "type": "process"},
                {"label": "機能別初期データ取得", "type": "process"},
                {"label": "初期表示データ返却", "type": "process"},
                {"label": "終了", "type": "terminal"},
            ],
        },
        "sequence": {
            "client_component": "外部データ取込画面",
            "api_title": "外部データ取込初期表示取得API",
            "steps": [
                {
                    "type": "request",
                    "step_no": 1,
                    "trigger": "画面初期表示",
                    "communication": "gaiBuDataTorikomi/show",
                    "description": "functionId を指定して初期表示 API を呼び出す",
                    "params": ["functionId"],
                    "details": [],
                    "db_accesses": [],
                },
                {
                    "type": "backend",
                    "step_no": 2,
                    "description": "ログイン情報から会社コード・アカウントID・基準日を補完し、共通設定を取得する",
                    "db_accesses": [
                        {"op": "r", "table": "subfunctions"},
                        {"op": "r", "table": "role_functions"},
                        {"op": "r", "table": "account_roles"},
                        {"op": "r", "table": "role_groups"},
                        {"op": "r", "table": "functions"},
                    ],
                    "details": [],
                },
                {
                    "type": "backend",
                    "step_no": 3,
                    "description": "functionId が支給データ取込の場合、体系一覧・体系項目一覧・支給年月一覧を取得する",
                    "db_accesses": [
                        {"op": "r", "table": "taikei"},
                        {"op": "r", "table": "taikei_item"},
                        {"op": "r", "table": "kyuyo_select"},
                        {"op": "r", "table": "system_codes"},
                        {"op": "r", "table": "rinji_select"},
                        {"op": "r", "table": "syoyo_select"},
                    ],
                    "details": [],
                },
                {
                    "type": "backend",
                    "step_no": 4,
                    "description": "functionId が就業勤務データ取込の場合、就業体系列表を取得する",
                    "db_accesses": [
                        {"op": "r", "table": "taikei_master"},
                    ],
                    "details": [],
                },
                {
                    "type": "response",
                    "step_no": 5,
                    "trigger": "初期表示データ反映",
                    "status": "httpレスポンス:200",
                    "description": "初期表示データを返却する",
                    "params": ["data"],
                },
            ],
        },
        "processing_detail": {
            "steps": [
                {
                    "title": "初期表示リクエストを受信する",
                    "content": [
                        "リクエストボディから functionId を取得する。",
                    ],
                },
                {
                    "title": "共通コンテキストを補完する",
                    "content": [
                        "ログイン情報から kaisyaCd と accountId を取得し、基準日 baseDate を設定する。",
                        {
                            "type": "mybatis_sql",
                            "mapper_xml": r"E:\WorkSpace\socia2026\back\back\src\main\java\jp\co\fminc\socia\common\service\DbCommonMapper.xml",
                            "statement_id": "getRoleFunctionDetails",
                            "table_labels": {
                                "subfunctions": "サブ機能",
                                "role_functions": "ロール機能権限",
                                "account_roles": "アカウントロール",
                                "functions": "機能",
                                "role_groups": "ロールグループ",
                            },
                            "column_labels": {
                                "functionid": "機能ID",
                                "subfunction_id": "サブ機能ID",
                                "permissionid": "権限ID",
                                "permission_type": "権限種類区分",
                                "permission": "起動可否",
                                "ipaddress_groupid": "IPアドレスグループID",
                                "strong_permissionid": "強権限ID",
                                "permissioncd_store": "権限コード格納値",
                                "groupid": "グループID",
                                "accountid": "アカウントID",
                                "fromymd": "有効開始日",
                                "toymd": "有効終了日",
                            },
                            "param_labels": {
                                "functionId": "機能ID",
                                "subFunctionId": "サブ機能ID",
                                "permissionIdList": "権限ID一覧",
                                "accountId": "アカウントID",
                                "baseDate": "基準日",
                            },
                        },
                        "Redis の defaultItemStyleJson から日付入力用 itemStyleJson を取得する。",
                        "取込詳細設定の narrow range と default 値を resultMap に格納する。",
                    ],
                },
                {
                    "title": "functionId 別の初期データを取得する",
                    "children": [
                        {
                            "title": "gaibuDataTorikomiPayment の場合",
                            "content": [
                                f"{detail_labels['taikeiList']}を取得する。",
                                {
                                    "type": "mybatis_sql",
                                    "mapper_xml": r"E:\WorkSpace\socia2026\back\back\src\main\java\jp\co\fminc\socia\kyuyo\kakuninoutput\mapper\KakuninOutputMapper.xml",
                                    "statement_id": "selectPaymentTypeAndTaikeiList",
                                    "table_labels": {
                                        "taikei": "体系",
                                    },
                                    "column_labels": {
                                        "payment_type": "支給区分",
                                        "taikei_code": "体系コード",
                                        "taikei_name": "体系名称",
                                        "kaisyacd": "会社コード",
                                        "hidden": "非表示フラグ",
                                        "fromymd": "有効開始日",
                                        "toymd": "有効終了日",
                                    },
                                    "param_labels": {
                                        "kaisyaCd": "会社コード",
                                        "baseDate": "基準日",
                                    },
                                },
                                f"{detail_labels['taikeiItemList']}を取得する。",
                                {
                                    "type": "mybatis_sql",
                                    "mapper_xml": r"E:\WorkSpace\socia2026\back\back\src\main\java\jp\co\fminc\socia\kyuyo\gaibudatatorikomi\mapper\GaiBuDataTorikomiMapper.xml",
                                    "statement_id": "selectTaikeiFieldItems",
                                    "table_labels": {
                                        "taikei_item": "体系項目",
                                    },
                                    "column_labels": {
                                        "kaisyacd": "会社コード",
                                        "payment_type": "支給区分",
                                        "payment_deduction_type": "支給控除区分",
                                        "taikei_code": "体系コード",
                                        "taikei_item_code": "体系項目コード",
                                        "item_name": "項目名称",
                                        "item_shortname": "項目略称",
                                        "data_type": "データ型区分",
                                        "data_type_store_json": "データ型設定JSON",
                                        "dataseq": "データ連番",
                                        "hidden": "非表示フラグ",
                                        "kijyunbi": "基準日",
                                    },
                                    "param_labels": {
                                        "kaisyaCd": "会社コード",
                                        "hidden": "非表示フラグ",
                                        "paymentType": "支給区分",
                                        "kijyunbi": "基準日",
                                    },
                                },
                                f"{detail_labels['kyuyoPaymentNengetsuList']}、{detail_labels['rinjiPaymentNengetsuList']}、{detail_labels['syoyoPaymentNengetsuList']}を取得する。",
                                {
                                    "type": "mybatis_sql",
                                    "mapper_xml": r"E:\WorkSpace\socia2026\back\back\src\main\java\jp\co\fminc\socia\kyuyo\kakuninoutput\mapper\KakuninOutputMapper.xml",
                                    "statement_ids": [
                                        "selectKyuyoPaymentList",
                                        "selectRinjiPaymentList",
                                        "selectSyoyoPaymentList",
                                    ],
                                    "table_labels": {
                                        "kyuyo_select": "給与支給年月",
                                        "rinji_select": "臨時給与支給年月",
                                        "syoyo_select": "賞与支給年月",
                                        "system_codes": "システムコード",
                                    },
                                    "column_labels": {
                                        "year": "年",
                                        "month": "月",
                                        "shime_day": "締日",
                                        "payment_month_type": "支給月区分",
                                        "payment_date": "支給年月日",
                                        "shime_date": "締年月日",
                                        "confirm_type": "確定区分",
                                        "code_itemid": "コード項目ID",
                                        "code": "コード",
                                        "name": "名称",
                                    },
                                    "param_labels": {
                                        "kaisyacd": "会社コード",
                                    },
                                },
                            ],
                        },
                        {
                            "title": "gaibuDataTorikomiSyugyoKinmu の場合",
                            "content": [
                                f"{detail_labels['condition.taikeiMasterList']}を取得する。",
                                {
                                    "type": "mybatis_sql",
                                    "mapper_xml": r"E:\WorkSpace\socia2026\back\back\src\main\java\jp\co\fminc\socia\kyuyo\gaibudatatorikomi\mapper\GaiBuDataTorikomiMapper.xml",
                                    "statement_id": "selectTaikeiMasterList",
                                    "table_labels": {
                                        "taikei_master": "体系マスタ",
                                    },
                                    "column_labels": {
                                        "taikei_code": "体系コード",
                                        "taikei_name": "体系名称",
                                        "kaisyacd": "会社コード",
                                        "hidden": "非表示フラグ",
                                        "kijyunbi": "基準日",
                                        "sort_order": "並び順",
                                        "fromymd": "有効開始日",
                                        "toymd": "有効終了日",
                                    },
                                    "param_labels": {
                                        "kaisyaCd": "会社コード",
                                        "hidden": "非表示フラグ",
                                        "kijyunbi": "基準日",
                                    },
                                },
                            ],
                        },
                        {
                            "title": "gaibuDataTorikomiSyainKoumoku の場合",
                            "content": [
                                "社員項目取込の基本テンプレートを初期化する。",
                            ],
                        },
                    ],
                },
                {
                    "title": "レスポンスを返却する",
                    "content": [
                        "生成した resultMap を successResponse で包装して返却する。",
                    ],
                }
            ]
        },
    }


def _param_row(
    param_name: str,
    item_name: str,
    *,
    description: str,
    data_type: str = "String",
    data_length: str = "-",
    required: str = "△",
    note: str = "",
    example: str = "",
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    row = {
        "param_name": param_name,
        "item_name": item_name,
        "required": required,
        "data_type": data_type,
        "data_length": data_length,
        "description": description,
        "example": example,
        "note": note,
        "depth": 0,
    }
    if children:
        row["children"] = children
    return row


def _object_param_row(
    param_name: str,
    item_name: str,
    *,
    description: str,
    data_type: str = "Object",
    required: str = "△",
    note: str = "",
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _param_row(
        param_name,
        item_name,
        description=description,
        data_type=data_type,
        required=required,
        note=note,
        children=children,
    )


def _array_param_row(
    param_name: str,
    item_name: str,
    *,
    description: str,
    required: str = "△",
    note: str = "",
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return _param_row(
        param_name,
        item_name,
        description=description,
        data_type="Array",
        required=required,
        note=note,
        children=children,
    )


def _collect_param_item_names(
    params: list[dict[str, Any]],
    *,
    parent_path: str = "",
) -> dict[str, str]:
    labels: dict[str, str] = {}
    for param in params:
        param_name = str(param.get("param_name") or "").strip()
        item_name = str(param.get("item_name") or "").strip()
        if not param_name:
            continue

        path = f"{parent_path}.{param_name}" if parent_path else param_name
        if item_name:
            labels[path] = item_name
            labels.setdefault(param_name, item_name)
            if path.startswith("data."):
                labels.setdefault(path.removeprefix("data."), item_name)
            parts = path.split(".")
            if len(parts) >= 2:
                labels.setdefault(".".join(parts[-2:]), item_name)

        children = param.get("children")
        if isinstance(children, list):
            labels.update(_collect_param_item_names(children, parent_path=path))
    return labels


def _processing_detail_label_map(response_params: list[dict[str, Any]]) -> dict[str, str]:
    raw_labels = _collect_param_item_names(response_params)
    return {
        path: _normalize_processing_detail_label(label)
        for path, label in raw_labels.items()
    }


def _normalize_processing_detail_label(label: str) -> str:
    normalized = str(label or "").strip().rstrip("。")
    normalized = re.sub(r"^(支給データ取込用の)", "", normalized)
    normalized = re.sub(r"^(就業勤務モード用)", "", normalized)
    normalized = re.sub(r"^(就業勤務取込(?:で利用する)?の?)", "", normalized)
    normalized = re.sub(r"^(画面で利用する)", "", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or str(label or "").strip()


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _get_nested(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic api_config.json from analysis.json")
    parser.add_argument("analysis_json", help="analysis.json artifact path")
    parser.add_argument("-p", "--project-config", default=None, help="project_config.json path")
    parser.add_argument("-o", "--output", required=True, help="Output api_config.json path")
    args = parser.parse_args()

    analysis_payload = load_json(Path(args.analysis_json))
    project_config = load_json(Path(args.project_config)) if args.project_config else {}
    api_config = build_api_config(analysis_payload, project_config)
    save_json(Path(args.output), api_config)
    print(f"[ok] api config saved: {args.output}")


if __name__ == "__main__":
    main()
