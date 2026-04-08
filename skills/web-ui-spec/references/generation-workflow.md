# 生成ワークフロー

## 新規作成時

1. **ヒアリング**: 機能名、画面一覧、主要操作を確認
2. **Configを構築**: 下記の「Configフォーマット」に従ってJSONを用意
3. **スクリプト実行**: `scripts/generate_from_template.py` でExcel生成
   ```bash
   python scripts/generate_from_template.py config.json -o output.xlsx
   ```
4. **検証**: `scripts/validate_spec.py` で品質チェック
5. **出力**: ユーザーのワークスペースにコピー

## 検証時

1. **読み込み**: 既存Excelファイルをopenpyxlで解析
2. **スクリプト実行**: `scripts/validate_spec.py` でチェック
3. **レポート出力**: 問題点と改善提案を一覧化

## 補完時

1. **読み込み**: 既存ファイルの構造を完全に把握
2. **差分特定**: 不足している定義項目を特定
3. **追記**: 既存の書式・パターンに完全に合わせて追記
4. **検証**: 追記後の品質チェック
