#!/usr/bin/env python3
"""Quality gate for deterministic API config artifacts."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


QUALITY_GATE_MODES = {"off", "report", "strict"}
UNRESOLVED_MARKERS = (
    "to confirm",
    "todo",
    "tbd",
    "pending",
    "未確認",
    "要確認",
    "確認中",
)
GENERIC_FLOW_LABELS = {
    "開始",
    "リクエスト受信",
    "パラメーター確認",
    "内部処理実行",
    "レスポンス返却",
    "終了",
}
GENERIC_SEQUENCE_DESCRIPTIONS = {
    "入力された条件を受け取り、内部処理を開始する。",
    "処理結果を呼出元へ返却する。",
}
GENERIC_PROCESSING_CONTENT = {
    "取得結果をAPIレスポンス形式へ整形する。",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_api_quality(
    config: dict[str, Any],
    analysis_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    resolved_analysis_payload = analysis_payload or {}
    analysis_body = _analysis_body(resolved_analysis_payload)
    issues: list[dict[str, Any]] = []

    def add_issue(
        code: str,
        severity: str,
        message: str,
        *,
        path: str | None = None,
        strict_blocking: bool = False,
    ) -> None:
        issue = {
            "code": code,
            "severity": severity,
            "message": message,
            "strict_blocking": strict_blocking,
        }
        if path:
            issue["path"] = path
        issues.append(issue)

    _check_unresolved_markers(config, add_issue)
    _check_analysis_uncertainty(analysis_body, add_issue)
    _check_analysis_param_coverage(config, resolved_analysis_payload, analysis_body, add_issue)
    _check_generic_param_descriptions(config, add_issue)
    _check_generic_overview(config, add_issue)
    _check_generic_sequence(config, add_issue)
    _check_generic_processing_detail(config, add_issue)

    error_count = sum(1 for issue in issues if issue["severity"] == "error")
    warning_count = sum(1 for issue in issues if issue["severity"] == "warning")
    score = max(0, 100 - (error_count * 30) - (warning_count * 10))

    if error_count:
        status = "fail"
    elif warning_count:
        status = "review"
    else:
        status = "pass"

    return {
        "status": status,
        "score": score,
        "errors": error_count,
        "warnings": warning_count,
        "issues": issues,
    }


def enforce_quality_gate(
    report: dict[str, Any],
    *,
    mode: str,
) -> list[str]:
    if mode not in QUALITY_GATE_MODES:
        raise ValueError(f"Unsupported quality gate mode: {mode}")
    if mode == "off":
        return []

    issues = report.get("issues", [])
    warning_messages = [issue["message"] for issue in issues if issue["severity"] == "warning"]

    if mode == "strict":
        blocking = [
            issue["message"]
            for issue in issues
            if issue["severity"] == "error" or issue.get("strict_blocking")
        ]
        if blocking:
            raise ValueError("API quality gate failed: " + "; ".join(blocking))

    return warning_messages


def print_quality_report(report: dict[str, Any], *, mode: str) -> None:
    print(f"[quality] mode={mode} status={report['status']} score={report['score']}")
    for issue in report.get("issues", []):
        path_suffix = f" ({issue['path']})" if issue.get("path") else ""
        print(f"  [{issue['severity']}] {issue['code']}{path_suffix}: {issue['message']}")


def _analysis_body(payload: dict[str, Any]) -> dict[str, Any]:
    nested = payload.get("analysis")
    if isinstance(nested, dict):
        return nested
    return payload


def _check_unresolved_markers(
    config: dict[str, Any],
    add_issue,
) -> None:
    fields = [
        ("cover.api_name", _get_nested(config, "cover.api_name")),
        ("api_info.description", _get_nested(config, "api_info.description")),
        ("overview.summary", _get_nested(config, "overview.summary")),
    ]

    for path, value in fields:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        markers = [marker for marker in UNRESOLVED_MARKERS if marker in lowered]
        if markers:
            add_issue(
                "unresolved_text",
                "error",
                f"未確定の占位表現を含みます: {', '.join(markers)}",
                path=path,
                strict_blocking=True,
            )


def _check_analysis_uncertainty(
    analysis_body: dict[str, Any],
    add_issue,
) -> None:
    uncertain = analysis_body.get("uncertain", [])
    if uncertain:
        add_issue(
            "analysis_uncertain",
            "warning",
            f"分析結果に未確定事項があります: {' / '.join(str(item) for item in uncertain)}",
            path="analysis.uncertain",
            strict_blocking=True,
        )


def _check_analysis_param_coverage(
    config: dict[str, Any],
    analysis_payload: dict[str, Any],
    analysis_body: dict[str, Any],
    add_issue,
) -> None:
    selected_api_url = _selected_api_url(analysis_payload, analysis_body)
    for field_name in ("request_params", "response_params"):
        analysis_params = _relevant_analysis_param_names(
            analysis_body.get(field_name, []),
            field_name=field_name,
            selected_api_url=selected_api_url,
        )
        config_params = _config_param_names(config.get(field_name, []))
        missing = sorted(analysis_params - config_params)
        extras = sorted(config_params - analysis_params)

        if missing:
            add_issue(
                "analysis_param_missing",
                "error",
                f"{field_name} に分析結果の項目が未反映です: {', '.join(missing)}",
                path=field_name,
                strict_blocking=True,
            )

        if extras and analysis_params:
            add_issue(
                "analysis_param_extra",
                "warning",
                f"{field_name} に分析結果外の項目があります: {', '.join(extras)}",
                path=field_name,
                strict_blocking=False,
            )


def _check_generic_param_descriptions(
    config: dict[str, Any],
    add_issue,
) -> None:
    patterns = {
        "request_params": re.compile(r"^リクエストパラメーター（.+）$"),
        "response_params": re.compile(r"^レスポンス項目（.+）$"),
    }
    for field_name, pattern in patterns.items():
        for index, param in enumerate(config.get(field_name, [])):
            description = str(param.get("description") or "").strip()
            if description and pattern.match(description):
                add_issue(
                    "generic_param_description",
                    "warning",
                    "パラメーター説明がテンプレート文のままです",
                    path=f"{field_name}[{index}].description",
                    strict_blocking=True,
                )


def _check_generic_overview(
    config: dict[str, Any],
    add_issue,
) -> None:
    summary = str(_get_nested(config, "overview.summary") or "").strip()
    if summary.endswith("ためのAPI。"):
        add_issue(
            "generic_overview_summary",
            "warning",
            "概要が汎用テンプレート文のままです",
            path="overview.summary",
            strict_blocking=True,
        )

    labels = {
        str(step.get("label") or "").strip()
        for step in (_get_nested(config, "overview.flow_steps") or [])
        if isinstance(step, dict)
    }
    if labels and labels <= GENERIC_FLOW_LABELS:
        add_issue(
            "generic_overview_flow",
            "warning",
            "フロー概要が汎用ステップのみで構成されています",
            path="overview.flow_steps",
            strict_blocking=False,
        )


def _check_generic_sequence(
    config: dict[str, Any],
    add_issue,
) -> None:
    client_component = str(_get_nested(config, "sequence.client_component") or "").strip()
    if client_component == "呼出元画面":
        add_issue(
            "generic_client_component",
            "warning",
            "呼出元コンポーネントが特定されていません",
            path="sequence.client_component",
            strict_blocking=False,
        )

    for index, step in enumerate(_get_nested(config, "sequence.steps") or []):
        if not isinstance(step, dict):
            continue
        description = str(step.get("description") or "").strip()
        if description in GENERIC_SEQUENCE_DESCRIPTIONS:
            add_issue(
                "generic_sequence_description",
                "warning",
                "シーケンス説明が汎用テンプレート文のままです",
                path=f"sequence.steps[{index}].description",
                strict_blocking=False,
            )


def _check_generic_processing_detail(
    config: dict[str, Any],
    add_issue,
) -> None:
    steps = _get_nested(config, "processing_detail.steps") or []
    for index, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        title = str(step.get("title") or "").strip()
        if title in {"リクエスト受付", "レスポンス生成"}:
            add_issue(
                "generic_processing_title",
                "warning",
                "処理詳細タイトルが汎用テンプレート文のままです",
                path=f"processing_detail.steps[{index}].title",
                strict_blocking=False,
            )

        for content_index, content in enumerate(step.get("content", [])):
            if isinstance(content, str) and content in GENERIC_PROCESSING_CONTENT:
                add_issue(
                    "generic_processing_content",
                    "warning",
                    "処理詳細本文が汎用テンプレート文のままです",
                    path=f"processing_detail.steps[{index}].content[{content_index}]",
                    strict_blocking=False,
                )


def _get_nested(data: dict[str, Any], path: str) -> Any:
    current: Any = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _param_names(params: list[dict[str, Any]]) -> set[str]:
    names = set()
    for param in params:
        if not isinstance(param, dict):
            continue
        name = str(param.get("name") or param.get("param_name") or param.get("item_name") or "").strip()
        if name:
            names.add(name)
        children = param.get("children")
        if isinstance(children, list):
            names.update(_param_names(children))
    return names


def _config_param_names(params: list[dict[str, Any]]) -> set[str]:
    return _param_names(params)


def _selected_api_url(payload: dict[str, Any], analysis_body: dict[str, Any]) -> str:
    selected_api = payload.get("selected_api")
    if isinstance(selected_api, dict):
        selected_path = str(selected_api.get("path") or "").strip()
        if selected_path:
            return selected_path

    scope = analysis_body.get("scope", {})
    scoped_url = str(scope.get("url") or "").strip()
    if scoped_url:
        return scoped_url

    feature = str(payload.get("feature") or analysis_body.get("feature") or "").strip()
    if feature.startswith("/"):
        return feature
    return ""


def _relevant_analysis_param_names(
    params: list[dict[str, Any]],
    *,
    field_name: str,
    selected_api_url: str,
) -> set[str]:
    all_names = _param_names(params)
    normalized_url = selected_api_url.lower()
    if normalized_url == "/api/gaibudatatorikomi/show":
        if field_name == "request_params":
            return all_names & {"functionId"}
        if field_name == "response_params":
            return all_names & {
                "kinoPermissionMap",
                "itemStyleJson",
                "filePropertyNarrowRange",
                "filePropertyDefaults",
                "taikeiList",
                "taikeiItemList",
                "kyuyoPaymentNengetsuList",
                "rinjiPaymentNengetsuList",
                "syoyoPaymentNengetsuList",
                "condition",
            }
    return all_names


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate API config quality and optionally enforce a gate")
    parser.add_argument("api_config", help="api_config.json path")
    parser.add_argument("-a", "--analysis", default=None, help="analysis.json path")
    parser.add_argument(
        "--mode",
        choices=sorted(QUALITY_GATE_MODES),
        default="report",
        help="Quality gate mode",
    )
    parser.add_argument("-o", "--output", default=None, help="Write quality report JSON")
    args = parser.parse_args()

    config = load_json(Path(args.api_config))
    analysis_payload = load_json(Path(args.analysis)) if args.analysis else {}
    report = evaluate_api_quality(config, analysis_payload)
    print_quality_report(report, mode=args.mode)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    enforce_quality_gate(report, mode=args.mode)


if __name__ == "__main__":
    main()
