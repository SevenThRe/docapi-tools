# Excel フォーマット詳細仕様

openpyxlでUI設計書を生成する際の具体的なセル配置・書式定数。

## 命名規則

- ファイル名: `{番号}.UI設計書-{機能名}.xlsx`
- 画面ID: 英字小文字camelCase（例: `gensyokuArea`）
- API ID: `{画面ID}/{操作名}`（例: `gensyokuArea/showBlock`）
- メッセージID: `MSG_XXXXX`（5桁ゼロ埋め）

## 定数定義

```python
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

# フォント
FONT_COVER_TITLE = Font(name='Kosugi', size=36)
FONT_COVER_LABEL = Font(name='Kosugi', size=14)
FONT_SHEET_TITLE = Font(name='Kosugi', size=20)
FONT_BODY = Font(name='Kosugi', size=9)
FONT_HEADER_VAL = Font(name='Kosugi', size=8)
FONT_COPYRIGHT = Font(name='Kosugi', size=8)

# 背景色
FILL_HEADER = PatternFill('solid', fgColor='F2F2F2')

# 罫線
BORDER_THIN = Border(
    left=Side('thin'), right=Side('thin'),
    top=Side('thin'), bottom=Side('thin'))
BORDER_TB = Border(top=Side('thin'), bottom=Side('thin'))

# 配置
ALIGN_CV = Alignment(vertical='center')
ALIGN_LV = Alignment(horizontal='left', vertical='center')
ALIGN_CC = Alignment(horizontal='centerContinuous', vertical='center')
ALIGN_R = Alignment(horizontal='right')
```

## 表紙シート

```
列幅: A=19.18, B=27.0, C=112.82, D=19.18
行高: 1=124.5, 2=79.5, 3=99.75, 4-8=23.25, 9=16.3, 10=23.15

A2: "UI設計書" (36pt, centerContinuous)
B4: "ユーザー"       C4: {会社名}       (14pt)
B5: "プロジェクト"   C5: {プロジェクト}  (14pt)
B6: "システム"       C6: {システム名}    (14pt)
B7: "機能名"         C7: {機能名}       (14pt)
B8: "機能ID"         C8: {機能ID}       (14pt)
B10: "作成日"        C10: {日付}        (14pt)
B11: "作成者"        C11: {作成者}      (14pt)
B12: "更新日"        C12: {日付}        (14pt, 緑系)
B13: "更新者"        C13: {更新者}      (14pt, 緑系)
D15: "©{会社名}"    (8pt, right)
```

## 共通ヘッダー（行1〜7）

### 狭幅シート（機能概要、改定履歴）

```
列幅: A=10.18, B=14.45, C=2.82, H=111.63, I=3.18, J=11.82, K=11.82
版管理列: I, J, K

左上ブロック:
  A1:G1結合 = =表紙!A2     (9pt, F2F2F2背景, thin罫線)
  A2:      = =表紙!B4       B2:G2結合 = =表紙!C4    (8pt)
  A3:      = =表紙!B5       B3:G3結合 = =表紙!C5
  A4:      = =表紙!B6       B4:G4結合 = =表紙!C6
  A5:      = =表紙!B7       B5:G5結合 = =表紙!C7

タイトル: H1:H5結合 = {シートタイトル}  (20pt)

版管理: I1="版" J1="年月日" K1="作成/更新者"  (F2F2F2背景)
  I2="初" J2==表紙!C10 K2==表紙!C11
  I5="最終" J5==表紙!C12 K5==表紙!C13

行6: A6:K6結合（高さ4.5pt、上下thin）
行7: A7="チケットNo." B7="{サブラベル}" C7="{説明ラベル}"
     J7="改定日付" K7="改定者"  (全てF2F2F2背景)
```

### 広幅シート（画面レイアウト、処理詳細）

```
列幅: A=10.18, B=14.45, C=2.63, D〜AZ=13.0
版管理列: AX, AY, AZ

左上ブロック: 同上
タイトル: H1:AW5結合 = {シートタイトル}  (20pt)

版管理: AX1="版" AY1="年月日" AZ1="作成/更新者"
  AX2="初" AY2==表紙!C10 AZ2==表紙!C11
  AX5="最終" AY5==表紙!C12 AZ5==表紙!C13

行6: A6:AZ6結合
行7: A7="チケットNo." B7="{サブラベル}" C7:AX7結合="{説明ラベル}"
     AY7="改定日付" AZ7="改定者"
```

## 行7のサブラベル・説明ラベル

| シート | B7 | C7（説明） |
|--------|-----|-----------|
| 外部-機能概要 | オブジェクトNo. | 説明 |
| 外部-画面レイアウト | オブジェクトNo. | 説明 |
| 内部-処理詳細 | 参照オブジェクトNo. | 処理 |
| 改定履歴 | シート名 | 改定内容 |

## エラー定義テーブルのセル結合

```
H{n}:AF{n}結合  = "{エラー条件見出し}"   (F2F2F2背景)
AG{n}:AQ{n}結合 = "メッセージID"          (F2F2F2背景)
AR{n}:AW{n}結合 = "表示ｵﾌﾞｼﾞｪｸﾄNo"      (F2F2F2背景)

H{n+1}:AF{n+1}結合 = "{条件内容}"
AG{n+1}:AQ{n+1}結合 = "MSG_XXXXX"
AR{n+1}:AW{n+1}結合 = "{No}"             (省略可)
```

## 処理詳細のリクエスト/レスポンステーブル

```
E{n}:Q{n}結合  = "リクエストパラメーター"  (F2F2F2背景)
R{n}:AT{n}結合 = "値"                     (F2F2F2背景)

E{n}:Q{n}結合  = "画面項目"               (F2F2F2背景)
R{n}:AT{n}結合 = "レスポンスパラメーター"   (F2F2F2背景)
```

## 改定履歴の特殊数式

```
J5: =MAX($J$8:$J$876)                    ← 最終更新日の自動取得
K5: =VLOOKUP(J5,$J$8:$K$876,2,FALSE)     ← 最終更新者の自動取得
```
