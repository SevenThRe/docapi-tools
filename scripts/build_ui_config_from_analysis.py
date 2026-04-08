#!/usr/bin/env python3
"""
build_ui_config_from_analysis.py

analysis_result.json と front Vue ソースを元に、
generate_from_template.py が読める UI 設計書 config を生成する。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from analyze_code import extract_api_calls_from_vue
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from analyze_code import extract_api_calls_from_vue

try:
    from config_schema import validate_config
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    from config_schema import validate_config

try:
    from business_glossary import DEFAULT_DB_PATH as DEFAULT_GLOSSARY_DB_PATH
    from business_glossary import collect_labels as collect_glossary_labels
except ImportError:
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from business_glossary import DEFAULT_DB_PATH as DEFAULT_GLOSSARY_DB_PATH
        from business_glossary import collect_labels as collect_glossary_labels
    except ImportError:
        DEFAULT_GLOSSARY_DB_PATH = None
        collect_glossary_labels = None


TAG_INTEREST = {
    "input",
    "button",
    "table",
    "checkbox",
    "radio",
    "v-icon",
    "v-avatar",
    "v-dialog",
    "v-expansion-panels",
    "select",
}

TAG_TYPE_MAP = {
    "input": "テキスト入力",
    "button": "ボタン",
    "table": "テーブル",
    "checkbox": "チェックボックス",
    "radio": "ラジオボタン",
    "v-icon": "アイコン",
    "v-avatar": "アイコン",
    "v-dialog": "モーダル",
    "v-expansion-panels": "アコーディオン",
    "select": "セレクトボックス",
}

EVENT_LABELS = {
    "click": "クリック時",
    "change": "変更時",
    "input": "入力時",
    "mouseover": "マウスオーバー時",
    "mouseout": "マウスアウト時",
    "mousedown": "ドラッグ開始時",
    "chkOnChange": "選択変更時",
}

IGNORE_COMMENT_KEYWORDS = ("TODO", "実装中", "テーブル定義がまだ確定していない")


def load_json(path: Path) -> Dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def save_json(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="analysis_result.json から UI config を生成する")
    parser.add_argument("analysis_json", help="analyze_code.py の出力 JSON")
    parser.add_argument("-p", "--project-config", help="project_config.json", default=None)
    parser.add_argument("-o", "--output", required=True, help="生成する config JSON")
    parser.add_argument("--function-name", help="機能名 override")
    parser.add_argument("--function-id", help="機能ID override")
    parser.add_argument("--screen-name", help="画面名 override")
    parser.add_argument("--author", help="作成者 override")
    parser.add_argument("--glossary-db", help="業務用語 SQLite DB", default=None)
    args = parser.parse_args()

    analysis = load_json(Path(args.analysis_json))
    project_config = load_json(Path(args.project_config)) if args.project_config else {}

    config = build_ui_config(
        analysis=analysis,
        project_config=project_config,
        function_name_override=args.function_name,
        function_id_override=args.function_id,
        screen_name_override=args.screen_name,
        author_override=args.author,
        glossary_db_path=args.glossary_db,
    )

    errors, warnings = validate_config(config)
    if warnings:
        print(f"[warn] config warnings: {len(warnings)}")
        for warning in warnings:
            print(f"  - {warning}")
    if errors:
        print(f"[error] config errors: {len(errors)}")
        for error in errors:
            print(f"  - {error}")
        sys.exit(1)

    save_json(Path(args.output), config)
    print(f"[ok] config saved: {args.output}")
    print(f"  function_name: {config['cover']['function_name']}")
    print(f"  screen_count: {len(config.get('screens', []))}")
    print(f"  api_count: {len(config.get('processing', {}).get('apis', []))}")


def build_ui_config(
    *,
    analysis: Dict,
    project_config: Dict,
    function_name_override: Optional[str],
    function_id_override: Optional[str],
    screen_name_override: Optional[str],
    author_override: Optional[str],
    glossary_db_path: Optional[str],
) -> Dict:
    vue_files = _pick_vue_files(analysis)
    vue_meta = _extract_vue_meta(vue_files[0]) if vue_files else {}
    scope = analysis.get("scope", {})
    api_calls = _collect_api_calls(analysis, vue_files)
    system_name = _get_nested(project_config, "project.system_name") or "Socia2026"
    field_labels = _collect_component_field_labels(vue_files)
    glossary_labels = _collect_glossary_field_labels(
        glossary_db_path=glossary_db_path,
        system_name=system_name,
        analysis=analysis,
        vue_files=vue_files,
        api_calls=api_calls,
    )
    field_labels.update(glossary_labels)

    function_name = (
        function_name_override
        or vue_meta.get("function_summary")
        or scope.get("feature")
        or analysis.get("feature")
        or "未命名機能"
    )
    screen_name = (
        screen_name_override
        or vue_meta.get("screen_name")
        or _normalize_screen_name(function_name)
    )
    function_id = (
        function_id_override
        or _derive_function_id(scope, api_calls, vue_files, analysis)
    )

    objects = _build_screen_objects(vue_files, api_calls, field_labels)
    initial_display = _build_initial_display(screen_name, api_calls, analysis)
    processing_apis = _build_processing_apis(
        screen_name=screen_name,
        function_id=function_id,
        objects=objects,
        analysis=analysis,
        api_calls=api_calls,
        vue_files=vue_files,
        field_labels=field_labels,
    )

    project_name = _get_nested(project_config, "project.name") or "Socia2026"
    company_name = _get_nested(project_config, "company.name") or ""
    author_name = (
        author_override
        or _get_nested(project_config, "author.name")
        or "${username}"
    )

    sheet_name = f"外部-画面レイアウト-{screen_name}"

    return {
        "cover": {
            "company": company_name,
            "project": project_name,
            "system": system_name,
            "function_name": function_name,
            "function_id": function_id,
            "author": author_name,
            "create_date": date.today().isoformat(),
            "update_date": date.today().isoformat(),
            "update_author": author_name,
        },
        "overview": {
            "description": _build_overview_description(function_name, screen_name, analysis, api_calls),
            "screens": [
                {
                    "name": screen_name,
                    "descriptions": [
                        f"{screen_name} の UI 要素、表示制御、連携 API を scope 内コードから抽出した。",
                    ],
                }
            ],
            "flow_description": _build_flow_description(api_calls, analysis),
        },
        "screen_layouts": [
            {
                "template_sheet": "外部-画面レイアウト-現職エリア",
                "output_name": sheet_name,
            }
        ],
        "screens": [
            {
                "name": screen_name,
                "target_sheet": sheet_name,
                "screen_id": function_id,
                "screen_name": function_name,
                "initial_display": initial_display,
                "objects": objects,
            }
        ],
        "processing": {
            "component_name": f"{screen_name}（{function_id}）",
            "apis": processing_apis,
        },
    }


def _pick_vue_files(analysis: Dict) -> List[Path]:
    files = []
    for raw in analysis.get("discovered_files", {}).get("vue_components", []):
        path = Path(raw)
        if path.exists():
            files.append(path)
    if not files:
        for raw in analysis.get("front_sources", []):
            path = Path(raw)
            if path.suffix.lower() == ".vue" and path.exists():
                files.append(path)
    return _dedupe_paths(files)


def _extract_vue_meta(vue_file: Path) -> Dict[str, str]:
    text = _read_text(vue_file)
    summary = ""
    system_name = ""
    for match in re.finditer(r"\*\s*([^:：]+?)\s*[:：]\s*(.+)", text):
        key = match.group(1).strip()
        value = match.group(2).strip()
        if "機能要約" in key:
            summary = value
        elif "システム" in key:
            system_name = value
    return {
        "function_summary": summary,
        "system_name": system_name,
        "screen_name": _normalize_screen_name(summary) if summary else "",
    }


def _collect_api_calls(analysis: Dict, vue_files: Sequence[Path]) -> List[Dict]:
    collected = []
    index_map = {}

    def upsert(item: Dict) -> None:
        key = (item.get("method"), item.get("url"))
        if key not in index_map:
            index_map[key] = len(collected)
            collected.append(item)
            return

        current = collected[index_map[key]]
        merged = dict(current)
        for field in ("source_file", "confidence", "backend_service_method", "backend_service_file"):
            if item.get(field):
                merged[field] = item[field]
        for field in ("request_keys_vue", "response_keys_vue", "request_keys_backend", "response_keys_backend"):
            values = []
            for source in (current.get(field, []), item.get(field, [])):
                for value in source:
                    if value not in values:
                        values.append(value)
            merged[field] = values
        collected[index_map[key]] = merged

    scope_url = analysis.get("scope", {}).get("url")
    if scope_url:
        upsert({
            "method": "POST",
            "url": scope_url,
            "request_keys_vue": [],
            "response_keys_vue": [],
            "request_keys_backend": [],
            "response_keys_backend": [],
            "source_file": "",
            "confidence": "HIGH",
        })
    for item in analysis.get("api_calls", []):
        upsert(item)
    for vue_file in vue_files:
        for item in extract_api_calls_from_vue(vue_file):
            upsert(item)
    return collected


def _derive_function_id(scope: Dict, api_calls: Sequence[Dict], vue_files: Sequence[Path], analysis: Dict) -> str:
    for item in api_calls:
        api_id = _api_id(item.get("url", ""))
        if api_id:
            base = api_id.split("/", 1)[0]
            if base:
                return base
    if scope.get("url"):
        api_id = _api_id(scope["url"])
        if api_id:
            return api_id.split("/", 1)[0]
    if vue_files:
        return vue_files[0].stem
    raw_feature = scope.get("feature") or analysis.get("feature") or "screen"
    return re.sub(r"[^A-Za-z0-9]+", "", raw_feature) or "screen"


def _normalize_screen_name(function_name: str) -> str:
    cleaned = re.sub(r"^\([^)]*\)", "", function_name or "").strip()
    cleaned = cleaned.strip("「」『』\"'")
    return cleaned or function_name or "画面"


def _build_overview_description(function_name: str, screen_name: str, analysis: Dict, api_calls: Sequence[Dict]) -> str:
    parts = [
        f"{function_name} の UI 設計書。",
        f"{screen_name} に関する front/back の指定 scope だけを対象にコード解析した。",
    ]
    if api_calls:
        parts.append(f"連携 API は {len(api_calls)} 件を検出した。")
    uncertain = analysis.get("uncertain", [])
    if uncertain:
        parts.append("未確定項目は別途確認対象とする。")
    return " ".join(parts)


def _build_flow_description(api_calls: Sequence[Dict], analysis: Dict) -> str:
    if api_calls:
        api_ids = ", ".join(_api_id(item.get("url", "")) for item in api_calls[:3] if item.get("url"))
        return f"画面初期表示または操作時に API を呼び出し、取得結果を画面項目へ反映する。対象 API: {api_ids}"
    if analysis.get("scope", {}).get("url"):
        return f"画面操作時に {analysis['scope']['url']} を呼び出し、結果を画面へ反映する。"
    return "画面の表示内容は指定 scope のコード解析結果をもとに構成する。"


def _build_initial_display(screen_name: str, api_calls: Sequence[Dict], analysis: Dict) -> Dict:
    actions = [f"{screen_name} を初期表示する。"]
    if analysis.get("scope", {}).get("files"):
        actions.append("指定された front コンポーネントを起点に表示内容を構築する。")
    apis = []
    for item in api_calls[:2]:
        api_id = _api_id(item.get("url", ""))
        if api_id:
            apis.append(f"表示API(id：{api_id})を呼出す。")
    return {"actions": actions, "api": apis}


def _build_screen_objects(
    vue_files: Sequence[Path],
    api_calls: Sequence[Dict],
    field_labels: Dict[str, str],
) -> List[Dict]:
    objects: List[Dict] = []
    seen = set()
    for vue_file in vue_files:
        for obj in _extract_objects_from_vue(vue_file, api_calls, field_labels):
            key = (obj["name"], obj["type"])
            if key in seen:
                continue
            seen.add(key)
            objects.append(obj)
            if len(objects) >= 20:
                break
        if len(objects) >= 20:
            break

    if not objects:
        objects.append(
            {
                "no": 1,
                "name": "主要表示領域",
                "type": "エリア",
                "attributes": [],
                "actions": ["指定 scope のコードから抽出した表示内容を描画する。"],
                "display_controls": [],
                "api": _build_default_api_ref(api_calls),
                "error_definitions": [],
            }
        )

    for index, obj in enumerate(objects, start=1):
        obj["no"] = index
    return objects


def _extract_objects_from_vue(
    vue_file: Path,
    api_calls: Sequence[Dict],
    field_labels: Dict[str, str],
) -> List[Dict]:
    content = _read_text(vue_file)
    template = _extract_template_block(content)
    if not template:
        return []

    objects = []
    recent_comment = ""
    for raw_line in template.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        comment_match = re.search(r"<!--\s*(.*?)\s*-->", line)
        if comment_match:
            comment = comment_match.group(1).strip()
            if comment and not any(keyword in comment for keyword in IGNORE_COMMENT_KEYWORDS):
                recent_comment = comment
            continue

        tag_match = re.search(r"<([A-Za-z][A-Za-z0-9-]*)\b([^>]*)>", line)
        if not tag_match or line.startswith("</"):
            continue

        tag = tag_match.group(1)
        attrs = tag_match.group(2)
        if not _is_candidate_object(tag, attrs, recent_comment):
            continue

        name = _derive_object_name(line, attrs, recent_comment, tag, field_labels)
        if not name or _looks_like_noise_name(name):
            continue

        obj_type = _derive_object_type(tag, attrs)
        actions = _derive_object_actions(tag, attrs, line)
        display_controls = _derive_display_controls(attrs)
        attributes = _derive_attributes(attrs, line)
        api_ref = _match_api_reference(name, actions, api_calls)
        error_defs = _derive_error_definitions(name, actions, template)

        objects.append(
            {
                "no": 0,
                "name": name,
                "type": obj_type,
                "attributes": attributes,
                "actions": actions,
                "display_controls": display_controls,
                "api": api_ref,
                "error_definitions": error_defs,
            }
        )
        recent_comment = ""

    return objects


def _extract_template_block(content: str) -> str:
    match = re.search(r"<template>([\s\S]*?)</template>", content)
    return match.group(1) if match else ""


def _is_candidate_object(tag: str, attrs: str, recent_comment: str) -> bool:
    if tag in TAG_INTEREST:
        return True
    if tag == "div" and any(token in attrs for token in ("@click", "v-if", "v-show", "id=", "class=")):
        return True
    if recent_comment and tag in {"div", "template"}:
        return "@" in attrs or "v-if" in attrs or "v-show" in attrs
    return False


def _derive_object_name(
    line: str,
    attrs: str,
    recent_comment: str,
    tag: str,
    field_labels: Dict[str, str],
) -> str:
    visible_text = _clean_visible_text(re.sub(r"<[^>]+>", "", line).strip(), field_labels)
    attr_id = _find_attr(attrs, "id")
    attr_class = _find_attr(attrs, "class")

    if visible_text and not _looks_like_noise_name(visible_text):
        return visible_text[:40]
    if recent_comment:
        if attr_id:
            return f"{recent_comment}（{attr_id}）"
        if attr_class:
            first_class = attr_class.split()[0]
            return f"{recent_comment}（{first_class}）"
        return recent_comment[:40]
    for attr_value in (attr_id, attr_class.split()[0] if attr_class else ""):
        label = _field_label(attr_value, field_labels)
        if label:
            return label
    if attr_id:
        return attr_id
    if attr_class:
        return attr_class.split()[0]
    return tag


def _derive_object_type(tag: str, attrs: str) -> str:
    if tag == "div":
        class_name = _find_attr(attrs, "class") or ""
        if "icon" in class_name:
            return "アイコン"
        if "dialog" in class_name:
            return "モーダル"
        if "table" in class_name:
            return "テーブル"
        if "@click" in attrs:
            return "ボタン"
        return "エリア"
    if tag == "input":
        input_type = _find_attr(attrs, "type") or ""
        if input_type == "file":
            return "ファイルアップロード"
        return "テキスト入力"
    return TAG_TYPE_MAP.get(tag, "エリア")


def _derive_object_actions(tag: str, attrs: str, line: str) -> List[str]:
    actions = []
    for event, expr in re.findall(r"@([A-Za-z0-9_:-]+)\s*=\s*\"([^\"]+)\"", attrs):
        label = EVENT_LABELS.get(event, f"{event} 時")
        handler = expr.strip()
        actions.append(f"{label}、{_normalize_handler_text(handler)} を実行する。")
    if not actions:
        if tag == "input":
            actions.append("入力値を保持する。")
        elif tag == "table":
            actions.append("一覧データを表示する。")
        elif tag == "v-dialog":
            actions.append("条件を満たす場合、ダイアログを表示する。")
        elif "<img" in line:
            actions.append("画像を表示する。")
    return actions


def _derive_display_controls(attrs: str) -> List[str]:
    controls = []
    for directive in ("v-if", "v-show", "v-else-if"):
        value = _find_attr(attrs, directive)
        if value:
            controls.append(f"{value} の場合に表示する。")
    disabled_value = _find_attr(attrs, ":disabled") or _find_attr(attrs, "disabled")
    if disabled_value:
        controls.append(f"{disabled_value} の場合に非活性とする。")
    class_value = _find_attr(attrs, ":class")
    if class_value and "disabled" in class_value:
        controls.append("条件に応じて活性/非活性を切り替える。")
    return controls


def _derive_attributes(attrs: str, line: str) -> List[str]:
    items = []
    for attr_name in ("id", "class", "type", "v-model", ":value"):
        value = _find_attr(attrs, attr_name)
        if value:
            label = attr_name.replace(":", "")
            items.append(f"{label}: {value}")
    if "persistent" in attrs:
        items.append("persistent ダイアログ")
    if "multiple" in attrs:
        items.append("複数選択")
    if "<input" in line and "type=\"text\"" in line:
        items.append("テキスト入力")
    return items


def _match_api_reference(name: str, actions: Sequence[str], api_calls: Sequence[Dict]) -> str:
    if not api_calls:
        return ""

    haystack = f"{name} {' '.join(actions)}".lower()
    for item in api_calls:
        url = item.get("url", "")
        api_id = _api_id(url)
        op = api_id.split("/")[-1].lower() if api_id else ""
        if op and op in haystack:
            return f"id：{api_id}"

    if len(api_calls) == 1 and actions:
        return f"id：{_api_id(api_calls[0].get('url', ''))}"
    return ""


def _derive_error_definitions(name: str, actions: Sequence[str], template: str) -> List[Dict]:
    errors = []
    haystack = f"{name} {' '.join(actions)}"
    if ("検索" in haystack or "search" in haystack.lower()) and "dataNone" in template:
        errors.append(
            {
                "condition": "該当データが存在しない場合",
                "message_id": "MSG_TBD",
                "object_no": "",
            }
        )
    if ("登録" in haystack or "save" in haystack.lower()) and "syainItemError" in template:
        errors.append(
            {
                "condition": "入力エラーが存在する場合",
                "message_id": "MSG_TBD",
                "object_no": "",
            }
        )
    return errors


def _build_processing_apis(
    *,
    screen_name: str,
    function_id: str,
    objects: Sequence[Dict],
    analysis: Dict,
    api_calls: Sequence[Dict],
    vue_files: Sequence[Path],
    field_labels: Dict[str, str],
) -> List[Dict]:
    request_params = analysis.get("request_params", [])
    response_params = analysis.get("response_params", [])
    object_names = [obj.get("name", "") for obj in objects]
    component_data_fields = _collect_component_data_fields(vue_files)

    apis = []
    for idx, call in enumerate(api_calls[:5]):
        api_url = call.get("url", "")
        api_id = _api_id(api_url)
        op_name = _operation_label(api_id)
        selected_request_params = _select_request_params(call, request_params, field_labels)
        selected_response_params = _select_response_params(
            call,
            response_params,
            object_names,
            field_labels,
            component_data_fields=component_data_fields,
        )

        apis.append(
            {
                "name": f"{screen_name}-{op_name}",
                "id": api_id,
                "action_type": "初期表示" if idx == 0 and "show" in api_id.lower() else op_name,
                "action_number": None if idx == 0 else idx,
                "request_params": selected_request_params,
                "response_description": f"{screen_name} に必要なデータを取得し、画面へ反映する。",
                "display_mode": _display_mode(api_id),
                "response_params": selected_response_params,
                "notes": _build_processing_notes(analysis),
            }
        )

    if not apis:
        apis.append(
            {
                "name": f"{screen_name}-表示",
                "id": function_id,
                "action_type": "初期表示",
                "request_params": _select_request_params({}, request_params, field_labels),
                "response_description": f"{screen_name} の表示データを取得する。",
                "display_mode": "■ブロック表示",
                "response_params": _select_response_params({}, response_params, object_names, field_labels),
                "notes": _build_processing_notes(analysis),
            }
        )

    return apis


def _select_request_params(
    call: Dict,
    request_params: Sequence[Dict],
    field_labels: Dict[str, str],
) -> List[Dict]:
    selected_names = []
    for key in call.get("request_keys_backend", [])[:12]:
        if key not in selected_names:
            selected_names.append(key)
    for key in call.get("request_keys_vue", [])[:12]:
        if key not in selected_names:
            selected_names.append(key)
    return [
        {
            "name": name,
            "description": _request_description(name, field_labels),
        }
        for name in selected_names[:15]
    ]


def _select_response_params(
    call: Dict,
    response_params: Sequence[Dict],
    object_names: Sequence[str],
    field_labels: Dict[str, str],
    *,
    component_data_fields: Sequence[str] = (),
) -> List[Dict]:
    selected = []
    used_items = set()
    selected_param_names = []
    for key in call.get("response_keys_backend", [])[:20]:
        if key not in selected_param_names:
            selected_param_names.append(key)
    for key in call.get("response_keys_vue", [])[:20]:
        if key not in selected_param_names:
            selected_param_names.append(key)
    if component_data_fields and len(selected_param_names) > 20:
        selected_lookup = {_normalize_field_key(key): key for key in selected_param_names}
        filtered = []
        for key in component_data_fields:
            matched = selected_lookup.get(_normalize_field_key(key))
            if matched and matched not in filtered:
                filtered.append(matched)
        if filtered:
            selected_param_names = filtered
    for param_name in selected_param_names[:20]:
        screen_item = _match_screen_item(param_name, object_names, used_items, field_labels)
        selected.append(
            {
                "screen_item": screen_item,
                "response_param": param_name,
                "note": "",
            }
        )
    return selected


def _match_screen_item(
    param_name: str,
    object_names: Sequence[str],
    used_items: set,
    field_labels: Dict[str, str],
) -> str:
    normalized_param = _normalize_field_key(param_name)
    label = _field_label(param_name, field_labels)
    for name in object_names:
        if _looks_like_noise_name(name):
            continue
        normalized_name = re.sub(r"[^a-z0-9一-龥ぁ-んァ-ヴ]", "", name.lower())
        if normalized_param and (normalized_param in normalized_name or normalized_name in normalized_param):
            used_items.add(name)
            return name
        if label and label in name:
            used_items.add(name)
            return name
    if label:
        return label
    return param_name


def _build_processing_notes(analysis: Dict) -> List[str]:
    return [item for item in analysis.get("uncertain", [])[:3]]


def _collect_component_data_fields(vue_files: Sequence[Path]) -> List[str]:
    fields = []
    pattern = re.compile(r'localData(?:\.value)?(?:\?)?\.(?:value\.)?([A-Za-z_][A-Za-z0-9_]*)')
    for vue_file in vue_files:
        content = _read_text(vue_file)
        for match in pattern.finditer(content):
            field = match.group(1)
            if field not in fields:
                fields.append(field)
    return fields


def _collect_component_field_labels(vue_files: Sequence[Path]) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    for vue_file in vue_files:
        content = _read_text(vue_file)
        for field_name, label in _extract_typescript_field_labels(content).items():
            labels.setdefault(field_name, label)
    return labels


def _collect_glossary_field_labels(
    *,
    glossary_db_path: Optional[str],
    system_name: str,
    analysis: Dict,
    vue_files: Sequence[Path],
    api_calls: Sequence[Dict],
) -> Dict[str, str]:
    if collect_glossary_labels is None:
        return {}

    db_path = Path(glossary_db_path) if glossary_db_path else DEFAULT_GLOSSARY_DB_PATH
    if not db_path or not Path(db_path).exists():
        return {}

    return collect_glossary_labels(
        Path(db_path),
        system_name=system_name,
        domain_candidates=_build_glossary_domain_candidates(analysis, vue_files, api_calls),
    )


def _build_glossary_domain_candidates(
    analysis: Dict,
    vue_files: Sequence[Path],
    api_calls: Sequence[Dict],
) -> List[str]:
    candidates: List[str] = []
    scope = analysis.get("scope", {})
    for item in (
        scope.get("feature"),
        analysis.get("feature"),
        analysis.get("function_name"),
    ):
        if item:
            candidates.append(str(item))
    for vue_file in vue_files:
        candidates.append(vue_file.stem)
        candidates.append(vue_file.parent.name)
    for api_call in api_calls:
        api_id = _api_id(api_call.get("url", ""))
        if api_id:
            candidates.append(api_id)
            candidates.extend(part for part in api_id.split("/") if part)
    return list(dict.fromkeys([item for item in candidates if item]))


def _extract_typescript_field_labels(content: str) -> Dict[str, str]:
    labels: Dict[str, str] = {}
    in_comment = False
    comment_lines: List[str] = []
    pending_label = ""

    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("/**"):
            in_comment = True
            comment_lines = []
            tail = stripped[3:]
            if "*/" in tail:
                comment_part = tail.split("*/", 1)[0]
                text = _clean_comment_text(comment_part)
                if text:
                    comment_lines.append(text)
                in_comment = False
                pending_label = _pick_comment_label(comment_lines)
            continue
        if in_comment:
            body = stripped
            if "*/" in body:
                body = body.split("*/", 1)[0]
                in_comment = False
            text = _clean_comment_text(body)
            if text:
                comment_lines.append(text)
            if not in_comment:
                pending_label = _pick_comment_label(comment_lines)
            continue

        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", stripped)
        if match and pending_label:
            labels.setdefault(match.group(1), pending_label)
            pending_label = ""
        elif stripped.startswith("interface ") or stripped in {"{", "}"}:
            continue
        else:
            pending_label = ""

    return labels


def _clean_comment_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("*"):
        cleaned = cleaned[1:].strip()
    return cleaned


def _pick_comment_label(comment_lines: Sequence[str]) -> str:
    for line in comment_lines:
        if line:
            return line
    return ""


def _clean_visible_text(text: str, field_labels: Dict[str, str]) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if not collapsed:
        return ""
    label = _resolve_expression_label(collapsed, field_labels)
    if label:
        return label
    without_mustache = re.sub(r"{{.*?}}", "", collapsed).strip()
    if without_mustache and not _looks_like_noise_name(without_mustache):
        return without_mustache
    return ""


def _resolve_expression_label(text: str, field_labels: Dict[str, str]) -> str:
    for field_name in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)", text):
        label = _field_label(field_name, field_labels)
        if label:
            return label
    return ""


def _looks_like_noise_name(name: str) -> bool:
    if not name:
        return True
    stripped = name.strip()
    noise_tokens = ("{{", "}}", "?.", "=>", "==", "&&", "||", "padStart(", "$emit(", "localData", "props.", "item.", "row.")
    if any(token in stripped for token in noise_tokens):
        return True
    if stripped in {"div", "template", "span", "icon"}:
        return True
    if stripped.startswith(("sscy-", "base-", "'", "\"")):
        return True
    if re.fullmatch(r"[a-z0-9_-]+", stripped) and ("-" in stripped or "_" in stripped):
        return True
    if len(stripped) <= 2 and not re.search(r"[一-龥ぁ-んァ-ヴ]", stripped):
        return True
    return False


def _normalize_field_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _field_label(field_name: str, field_labels: Dict[str, str]) -> str:
    if not field_name:
        return ""
    normalized = _normalize_field_key(field_name)
    for key, label in field_labels.items():
        if _normalize_field_key(key) == normalized:
            return label
    return ""


def _humanize_identifier(field_name: str) -> str:
    if not field_name:
        return ""
    if "_" in field_name:
        return field_name.replace("_", " ")
    return re.sub(r"(?<!^)([A-Z])", r" \1", field_name).strip()


def _request_description(field_name: str, field_labels: Dict[str, str]) -> str:
    label = _field_label(field_name, field_labels)
    if label:
        return f"{label}（{field_name}）をリクエストに設定する。"
    return f"パラメーター（{field_name}）をリクエストに設定する。"




def _api_id(url: str) -> str:
    cleaned = url.strip()
    if cleaned.startswith("/api/"):
        return cleaned[len("/api/"):]
    return cleaned.lstrip("/")


def _operation_label(api_id: str) -> str:
    last = api_id.split("/")[-1] if api_id else "表示"
    lowered = last.lower()
    if "showblock" in lowered or "show" in lowered:
        return "表示"
    if "search" in lowered or "list" in lowered:
        return "検索"
    if "save" in lowered or "regist" in lowered or "create" in lowered:
        return "登録"
    if "update" in lowered:
        return "更新"
    if "delete" in lowered or "remove" in lowered:
        return "削除"
    if "setting" in lowered:
        return "設定"
    return last


def _display_mode(api_id: str) -> str:
    lowered = api_id.lower()
    if "line" in lowered or "list" in lowered:
        return "■一覧表示"
    return "■ブロック表示"


def _build_default_api_ref(api_calls: Sequence[Dict]) -> str:
    if not api_calls:
        return ""
    return f"id：{_api_id(api_calls[0].get('url', ''))}"


def _normalize_handler_text(handler: str) -> str:
    text = handler.strip()
    if text.startswith("$emit"):
        return text.replace("$emit", "emit")
    return text


def _find_attr(attrs: str, attr_name: str) -> str:
    patterns = [
        rf'{re.escape(attr_name)}\s*=\s*"([^"]+)"',
        rf"{re.escape(attr_name)}\s*=\s*'([^']+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, attrs)
        if match:
            return match.group(1).strip()
    if re.search(rf"\b{re.escape(attr_name)}\b", attrs):
        return attr_name
    return ""


def _read_text(path: Path) -> str:
    for encoding in ("utf-8", "cp932", "euc-jp"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    result = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _get_nested(data: Dict, path: str):
    current = data
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


if __name__ == "__main__":
    main()
