#!/usr/bin/env python3
"""
config_schema.py - UI設計書Config JSONのバリデーション
=====================================================

Config JSON の必須フィールドを検証し、エラー・警告を返す。
LLM が Config を生成する際に間違ったフィールド名を使った場合の検出に使用する。
"""

from typing import Dict, List, Tuple

# (field_path, required, description)
REQUIRED_FIELDS = [
    # cover
    ("cover", True, "表紙情報"),
    ("cover.company", False, "会社名"),
    ("cover.project", True, "プロジェクト名"),
    ("cover.function_name", True, "機能名"),
    ("cover.function_id", True, "機能ID"),

    # overview
    ("overview", True, "機能概要"),
    ("overview.description", True, "機能概要説明文（H9）"),
    ("overview.screens", True, "画面一覧（配列）"),
    ("overview.flow_description", False, "処理フロー説明文（H19）"),

    # processing
    ("processing", True, "処理詳細"),
    ("processing.component_name", True, "コンポーネント名（B9: 例「現職エリア（gensyokuarea）」）"),
    ("processing.apis", True, "API定義配列"),
]

REQUIRED_API_FIELDS = [
    ("name", True, "API名"),
    ("action_type", False, "アクション名（デフォルト: 初期表示）"),
    ("request_params", True, "リクエストパラメーター配列"),
    ("response_params", True, "レスポンスパラメーター配列"),
]

REQUIRED_SCREEN_FIELDS = [
    ("name", True, "画面/コンポーネント名"),
    ("target_sheet", True, "対象レイアウトシート名（テンプレートの既存シート名）"),
    ("screen_id", True, "画面ID（英字、例: gensyokuarea）"),
    ("screen_name", True, "画面名（日本語、例: (共通)現職エリア）"),
    ("objects", True, "オブジェクト定義配列"),
]

REQUIRED_OBJECT_FIELDS = [
    ("no", True, "オブジェクトNo."),
    ("name", True, "オブジェクト名"),
    ("type", True, "種類（ボタン/アイコン/テキスト等）"),
    ("actions", True, "動作定義配列"),
]

KNOWN_OBJECT_TYPES = [
    "ボタン", "アイコン", "テキスト", "テキスト入力", "ラベル", "画像",
    "チェックボックス", "ラジオボタン", "セレクトボックス", "リスト",
    "テーブル", "パネル", "モーダル", "タブ", "アコーディオン",
    "ファイルアップロード", "リンク", "エリア",
]


def _get_nested(data: dict, path: str):
    """ドット区切りパスで値を取得"""
    parts = path.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def validate_config(config: dict) -> Tuple[List[str], List[str]]:
    """
    Config JSON を検証する。

    Returns:
        (errors, warnings): エラーのリスト, 警告のリスト
    """
    errors = []
    warnings = []

    # --- トップレベル必須フィールド ---
    for path, required, desc in REQUIRED_FIELDS:
        val = _get_nested(config, path)
        if val is None:
            if required:
                errors.append(f"必須フィールド '{path}' がありません — {desc}")
            else:
                warnings.append(f"推奨フィールド '{path}' がありません — {desc}")
        elif isinstance(val, str) and not val.strip():
            warnings.append(f"'{path}' が空文字です — {desc}")

    # --- overview.screens の検証 ---
    screens_overview = _get_nested(config, "overview.screens")
    if isinstance(screens_overview, list):
        for i, screen in enumerate(screens_overview):
            if not isinstance(screen, dict):
                errors.append(f"overview.screens[{i}] がオブジェクトではありません")
                continue
            if "name" not in screen:
                errors.append(f"overview.screens[{i}].name がありません")
            if "descriptions" not in screen:
                warnings.append(f"overview.screens[{i}].descriptions がありません")
            elif not isinstance(screen.get("descriptions"), list):
                errors.append(f"overview.screens[{i}].descriptions が配列ではありません")

    # --- screens の検証（画面レイアウト） ---
    screens = config.get("screens", [])
    if not screens:
        warnings.append("'screens' が空です — 画面レイアウトシートは生成されません")
    for i, screen in enumerate(screens):
        if not isinstance(screen, dict):
            errors.append(f"screens[{i}] がオブジェクトではありません")
            continue
        for field, required, desc in REQUIRED_SCREEN_FIELDS:
            if required and field not in screen:
                errors.append(f"screens[{i}].{field} がありません — {desc}")

        # オブジェクト定義の検証
        objects = screen.get("objects", [])
        if not objects:
            warnings.append(f"screens[{i}].objects が空です")
        for j, obj in enumerate(objects):
            if not isinstance(obj, dict):
                errors.append(f"screens[{i}].objects[{j}] がオブジェクトではありません")
                continue
            for field, required, desc in REQUIRED_OBJECT_FIELDS:
                if required and field not in obj:
                    errors.append(f"screens[{i}].objects[{j}].{field} がありません — {desc}")
            # 種類チェック
            obj_type = obj.get("type", "")
            if obj_type and obj_type not in KNOWN_OBJECT_TYPES:
                warnings.append(
                    f"screens[{i}].objects[{j}].type '{obj_type}' は未知の種類です。"
                    f"既知: {', '.join(KNOWN_OBJECT_TYPES[:5])}..."
                )
            # エラー定義の検証
            for k, err_def in enumerate(obj.get("error_definitions", [])):
                if "condition" not in err_def:
                    errors.append(f"screens[{i}].objects[{j}].error_definitions[{k}].condition がありません")
                if "message_id" not in err_def:
                    warnings.append(f"screens[{i}].objects[{j}].error_definitions[{k}].message_id がありません")

    # --- processing.apis の検証 ---
    apis = _get_nested(config, "processing.apis")
    if isinstance(apis, list):
        for i, api in enumerate(apis):
            if not isinstance(api, dict):
                errors.append(f"processing.apis[{i}] がオブジェクトではありません")
                continue
            for field, required, desc in REQUIRED_API_FIELDS:
                if required and field not in api:
                    errors.append(f"processing.apis[{i}].{field} がありません — {desc}")

            # request_params のチェック
            for j, rp in enumerate(api.get("request_params", [])):
                if "name" not in rp:
                    errors.append(f"processing.apis[{i}].request_params[{j}].name がありません")

            # response_params のチェック
            for j, rp in enumerate(api.get("response_params", [])):
                if "screen_item" not in rp:
                    errors.append(f"processing.apis[{i}].response_params[{j}].screen_item がありません")
                if "response_param" not in rp:
                    errors.append(f"processing.apis[{i}].response_params[{j}].response_param がありません")
    elif apis is not None:
        errors.append("processing.apis が配列ではありません")

    # --- screen_layouts の検証 ---
    layouts = config.get("screen_layouts", [])
    if layouts:
        for i, layout in enumerate(layouts):
            if "template_sheet" not in layout:
                errors.append(f"screen_layouts[{i}].template_sheet がありません")
            if "output_name" not in layout:
                errors.append(f"screen_layouts[{i}].output_name がありません")

    # --- screens ↔ processing の整合性チェック ---
    screen_names = set()
    for s in screens:
        if "name" in s:
            screen_names.add(s["name"])
    comp_name = _get_nested(config, "processing.component_name") or ""
    if comp_name and screen_names and not any(comp_name.startswith(sn.split("（")[0]) for sn in screen_names):
        warnings.append(
            f"processing.component_name '{comp_name}' が screens のどの name とも一致しません。"
            "screens と processing は同じ機能を表すべきです。"
        )

    return errors, warnings


def validate_and_report(config: dict) -> bool:
    """検証してコンソールに報告。成功=True"""
    errors, warnings = validate_config(config)

    if warnings:
        print(f"[warn] warnings: {len(warnings)}")
        for w in warnings:
            print(f"  [warn] {w}")

    if errors:
        print(f"[error] errors: {len(errors)}")
        for e in errors:
            print(f"  [error] {e}")
        return False

    print("[ok] config validation: OK")
    return True


if __name__ == "__main__":
    import sys
    import json
    if len(sys.argv) < 2:
        print("Usage: python config_schema.py config.json")
        sys.exit(1)
    with open(sys.argv[1], encoding="utf-8") as f:
        config = json.load(f)
    ok = validate_and_report(config)
    sys.exit(0 if ok else 1)
