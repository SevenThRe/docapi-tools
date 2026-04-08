#!/usr/bin/env python3
"""
batch_api_spec.py — API設計書バッチスキャナー

指定ディレクトリを走査し、API設計書として処理可能なJSONファイルの一覧を返す。
/docapi のバッチモード（チームモード）から呼び出される。

使い方:
  python3 batch_api_spec.py scan <dir> [--output-dir <out_dir>]
      → JSON設定ファイル一覧とそれぞれの出力パスをJSONで返す

  python3 batch_api_spec.py summary <results_file>
      → ワーカー実行結果ファイルを受け取り、サマリーを出力する
"""

import sys
import json
import re
from pathlib import Path
from datetime import datetime


def derive_operation_label(api_id: str) -> str:
    last = api_id.split('/')[-1] if api_id else '表示'
    lowered = last.lower()
    if 'showblock' in lowered or 'show' in lowered or lowered in {'get', 'detail'}:
        return '表示'
    if 'init' in lowered or 'reload' in lowered:
        return '初期表示'
    if 'search' in lowered or 'list' in lowered:
        return '検索'
    if 'save' in lowered or 'regist' in lowered or 'create' in lowered:
        return '登録'
    if 'update' in lowered:
        return '更新'
    if 'delete' in lowered or 'remove' in lowered:
        return '削除'
    if 'setting' in lowered:
        return '設定'
    return last


def is_api_config(path: Path) -> bool:
    """JSONファイルがAPI設計書設定として有効かチェックする"""
    try:
        with open(path, encoding='utf-8') as f:
            data = json.load(f)
        cover = data.get('cover', {})
        # cover.api_name か cover.api_id のどちらかがあればOK
        return bool(cover.get('api_name') or cover.get('api_id'))
    except Exception:
        return False


def api_id_to_filename(config_path: Path, data: dict) -> str:
    """設定JSONからExcel出力ファイル名を生成する"""
    cover = data.get('cover', {})
    api_id = cover.get('api_id', '')
    api_name = cover.get('api_name', '')
    feature_name = (cover.get('feature_name') or cover.get('function_name') or '').strip()
    operation_name = (cover.get('operation_name') or '').strip()
    spec_no = str(
        cover.get('spec_no')
        or cover.get('document_no')
        or cover.get('number')
        or ''
    ).strip().rstrip('.')

    if api_id and not operation_name:
        operation_name = derive_operation_label(api_id)

    name_parts = ['API設計書']
    if feature_name:
        name_parts.append(feature_name)
    if operation_name and operation_name != feature_name:
        name_parts.append(operation_name)
    elif api_name and not feature_name:
        name_parts.append(api_name)

    if len(name_parts) > 1:
        safe = re.sub(r'[/\\:*?"<>|]', '_', '-'.join(name_parts))
        if spec_no:
            return f"{spec_no}.{safe}.xlsx"
        return f"{safe}.xlsx"

    # fallback
    # フォールバック: JSONファイル名を使う
    return f"{config_path.stem}_API設計書.xlsx"


def scan_directory(target_dir: str, output_dir: str = None) -> None:
    """
    ディレクトリを走査してAPI設定JSONを探し、処理計画をJSONで出力する。

    出力フォーマット:
    {
      "scan_dir": "/path/to/kaisyasettei",
      "output_dir": "/path/to/output",
      "timestamp": "2026-02-25T12:00:00",
      "tasks": [
        {
          "index": 1,
          "config_path": "/path/to/config.json",
          "output_path": "/path/to/output/api_id_API設計書.xlsx",
          "api_name": "...",
          "api_id": "..."
        },
        ...
      ],
      "skipped": [
        {"path": "/path/to/other.json", "reason": "cover.api_name/api_id not found"}
      ]
    }
    """
    target = Path(target_dir).resolve()
    if not target.exists():
        print(json.dumps({"error": f"ディレクトリが見つかりません: {target_dir}"}))
        sys.exit(1)

    out_dir = Path(output_dir).resolve() if output_dir else target.parent / "output"

    tasks = []
    skipped = []

    # ディレクトリ以下のJSONを再帰的に探索（__pycache__ は除外）
    json_files = sorted([
        p for p in target.rglob("*.json")
        if "__pycache__" not in p.parts and not p.name.startswith("_")
    ])

    for i, json_path in enumerate(json_files, 1):
        try:
            with open(json_path, encoding='utf-8') as f:
                data = json.load(f)
            cover = data.get('cover', {})
            api_name = cover.get('api_name', '')
            api_id = cover.get('api_id', '')

            if not (api_name or api_id):
                skipped.append({
                    "path": str(json_path),
                    "reason": "cover.api_name / cover.api_id が見つかりません"
                })
                continue

            output_filename = api_id_to_filename(json_path, data)
            # 出力先は output_dir 直下にフラット配置
            output_path = out_dir / output_filename

            tasks.append({
                "index": i,
                "config_path": str(json_path),
                "output_path": str(output_path),
                "api_name": api_name,
                "api_id": api_id
            })

        except json.JSONDecodeError:
            skipped.append({
                "path": str(json_path),
                "reason": "JSONパースエラー"
            })
        except Exception as e:
            skipped.append({
                "path": str(json_path),
                "reason": str(e)
            })

    result = {
        "scan_dir": str(target),
        "output_dir": str(out_dir),
        "timestamp": datetime.now().isoformat(timespec='seconds'),
        "total_found": len(json_files),
        "total_tasks": len(tasks),
        "total_skipped": len(skipped),
        "tasks": tasks,
        "skipped": skipped
    }

    print(json.dumps(result, ensure_ascii=False, indent=2))


def summarize_results(results_file: str) -> None:
    """
    ワーカー実行結果ファイル（1行=1結果）を読み込んでサマリーを出力する。

    結果行フォーマット:
      OK: <output_path> [api_name: ..., api_id: ...]
      ERROR: <type>: <config_path> — <message>
    """
    results_path = Path(results_file)
    if not results_path.exists():
        print(json.dumps({"error": f"結果ファイルが見つかりません: {results_file}"}))
        sys.exit(1)

    success = []
    errors = []

    with open(results_path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("OK:"):
                success.append(line)
            elif line.startswith("ERROR:"):
                errors.append(line)

    summary = {
        "total": len(success) + len(errors),
        "success_count": len(success),
        "error_count": len(errors),
        "success": success,
        "errors": errors
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    if len(sys.argv) < 3:
        print("Usage:")
        print("  batch_api_spec.py scan <dir> [--output-dir <out_dir>]")
        print("  batch_api_spec.py summary <results_file>")
        sys.exit(1)

    mode = sys.argv[1]

    if mode == "scan":
        target_dir = sys.argv[2]
        output_dir = None
        if "--output-dir" in sys.argv:
            idx = sys.argv.index("--output-dir")
            output_dir = sys.argv[idx + 1]
        scan_directory(target_dir, output_dir)

    elif mode == "summary":
        results_file = sys.argv[2]
        summarize_results(results_file)

    else:
        print(f"Unknown mode: {mode}")
        sys.exit(1)


if __name__ == "__main__":
    main()
