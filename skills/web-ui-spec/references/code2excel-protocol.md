# Code2Excel 分析プロトコル

OpenAPI仕様が存在しない・エンドポイントがMap型（`Map<String, Object>`）で定義されているレガシーSIerプロジェクトにおいて、実コードからUI設計書を逆生成するための手順と判断ルールを定義する。

## 8.1 全体パイプラインとアーキテクチャ判断

```
[機能名 / 画面名 / エンドポイントURL] ← 起点（どれでも可）
           ↓
  [Phase 1: ファイル発見]          ← analyze_code.py（決定論的grep）
  Vue Component → Composable/API Service
  → エンドポイントURL → Spring Boot Controller
  → Service → Mapper → MyBatis XML
           ↓
  [Phase 2: パラメーター抽出]      ← LLM（Sonnet）
  Map.get/put の文字列リテラル収集
  MyBatis XML #{xxx} 収集（最高信頼度）
  Vue templateバインディング収集
           ↓
  [Phase 3: 信頼度スコアリング]    ← LLM判断 + ルールベース
  各パラメーターに HIGH/MEDIUM/LOW/UNKNOWN を付与
           ↓
  [Phase 4: Config組立]            ← LLM（Sonnet）
  comprehensive_sample.json 形式のJSONを生成
  LOW/UNKNOWN 項目は ※要確認 タグ付き
           ↓
  [Phase 5: Excel生成]             ← generate_from_template.py（決定論的）
  UI設計書.xlsx 出力
```

**なぜASTではなくgrepか**: Map型の場合、ASTを解析しても`Map<String, Object>`という型情報しか得られない。実際のキー名は`map.get("keyName")`の文字列リテラルの中にある。正規表現による文字列抽出の方が確実で高速。

**なぜCodexではなくSonnetか（デフォルト）**: ファイル発見後に関連ファイル群を束ねてコンテキストとして渡せば、3〜5層程度の呼び出し連鎖はSonnetで十分に解析できる。Codex(o3)投入が有効なのは「5層以上の連鎖・共通基底クラスへの依存・暗黙的パラメーター付加」など、コンテキストに収まりきらない複雑なケースに限定する。

## 8.2 ファイル発見戦略（5段階grep）

`analyze_code.py` がこの手順を自動実行する。手動の場合は以下の順序で辿る。

### Step 1: Vueコンポーネント特定

起点が機能名の場合（例: 「現職エリア」「gensyokuarea」）:
```bash
# 日本語・英字・camelCase で幅広くgrep
grep -rl "現職エリア\|gensyokuarea\|GensyokuArea" src/views src/components src/features
```

起点がURL（`/api/gensyoku/show`）の場合: Step 3から開始。

### Step 2: APIコール抽出（Vue → URL）

見つかったVueファイル・composable内のAPIコールを探す。**プロジェクトによってラッパー関数が異なる**点に注意:

```bash
# 標準パターン（axios/fetch系）
grep -n "axios\|useApi\|apiClient\|\$http" <vue_file>

# カスタムラッパーパターン（SIer案件で頻出）
grep -n "querypost\|queryget\|queryput\|querypostAbortable\|commonPost\|apiPost" <vue_file>

# URLベース変数の定義を確認
grep -n "const.*URL\|const.*Api\|const.*BASE" <vue_file>

# 見つかったimport先のAPIサービスファイルも確認
grep -rn "gensyoku\|/api/gensyoku" src/api src/composables src/services
```

**URLベース変数の展開（重要）**:

プロジェクトによっては以下のようにAPIのベースURLを定数で管理している:
```typescript
const API_URL = "/api/aplAprList"           // 定数宣言

querypost(API_URL + '/show', param, signal)  // 文字列結合でURL形成
// → 実際のURL: /api/aplAprList/show
```
この場合、ファイル先頭で `const API_URL` を検索してから、各コールのURLを展開する。`analyze_code.py` はこれを自動解決する。

**変数参照パターン（第2引数がオブジェクトではなく変数名の場合）**:

```typescript
// ❌ 直接リテラルがない → "(変数参照)" と表示される
querypost(API_URL + '/show', param, signal)

// ✅ 上記の場合、同関数内（2000文字以内）を遡って変数定義を探す
const param: any = {
  syainseq: store.headerInfo.syainseq,
  accountid: store.headerInfo.accountid,
  kaisyaCd:  store.headerInfo.kaisyaCd,
  functionId: functionId.value,
  initFlag:  initFlag.value,
  menuId:    route.params.menuId,
  pageMode:  pageMode.value,
}
// → これらのキーをリクエストパラメーターとして抽出
```

**レスポンスキー抽出（.then()コールバック）**:

```typescript
querypost(API_URL + '/show', param)
  .then((res: any) => {
    execTypeList.value      = res.data.execTypeList      // ← キー抽出
    flowTypeList.value      = res.data.flowTypeList      // ← キー抽出
    syainApplicationsList   = res.data.syainApplicationsList
    AprExectorList.value    = res.data.AprExectorList
    calendarItemStyleJson   = res.data.calendarItemStyleJson
  })
```
`.then()` / `await` の後の `res.data.xxx` または `response.data.xxx` を全収集する。

抽出対象:
- エンドポイントURLパス（例: `/api/gensyokuarea/showBlock`）
- リクエストボディのオブジェクトキー（例: `{ empCodeList: [...], functionId: x }`）
- レスポンスの参照パス（例: `res.data.result.titleLabel`, `res.data.execTypeList`）

### Step 3: Spring Boot Controller特定（URL → Controller）

```bash
# URLパスの末尾部分でgrepする（/api/プレフィックスは除外）
grep -rn "showBlock\|gensyokuarea" src/main/java --include="*.java"
# @RequestMapping/@PostMapping/@GetMapping を確認
grep -n "@.*Mapping\|@RestController" <controller_file>
```

### Step 4: Service → Mapper連鎖追跡

```bash
# Controller内のServiceメソッド呼び出しを確認
grep -n "service\.\|Service\." <controller_file>
# Service実装クラスを特定
grep -rn "class.*ServiceImpl\|interface.*Service" src/main/java --include="*.java"
# Mapper呼び出しを特定
grep -n "mapper\.\|Mapper\." <service_file>
```

### Step 5: MyBatis XML収集（最終的なパラメーター名の正解）

```bash
# Mapperインターフェース名からXMLを特定
find src/main/resources -name "*.xml" | xargs grep -l "GensyokuMapper\|gensyoku"
# #{xxx} パターンを全収集
grep -o '#{[^}]*}' <mapper_xml_file>
```

## 8.3 Mapキー収集ルール（信頼度付き）

各ソースからパラメーターを収集し、以下の信頼度を付与する:

| ソース | 信頼度 | 根拠 |
|--------|--------|------|
| MyBatis XML `#{keyName}` | **HIGH** | SQLに直結。リネームされることはない |
| Service/Mapper `map.get("keyName")` | **HIGH** | 実際に値を取り出している |
| Controller `map.get("keyName")` | **MEDIUM** | バリデーション前のキー。ServiceでRenameされる可能性あり |
| Vue request body変数 `{ keyName: val }` | **MEDIUM** | フロントが送っているが、Javaで別名にマップされる可能性あり |
| Vue `.then()` `res.data.keyName` | **MEDIUM** | レスポンスキー名として実際に使用。Service内変換の可能性あり |
| Vue template `{{ item.keyName }}` / `v-model` | **MEDIUM** | レスポンスキー名として使われているが、Service内で変換される可能性あり |
| Controller `@PathVariable` | **HIGH** | URLパスパラメーター。URLパスに直接現れる（型安全）|
| コメントや変数名からの推測 | **LOW** | 実際のキー名は未確認 |
| コンテキストからのAI推測 | **UNKNOWN** | 必ず ※要確認 タグをつける |

**除外すべきパラメーター（サーバー注入）**:

以下はバックエンドが自動付与するパラメーターであり、クライアントからは送信されない。設計書の「クライアント送信」欄には含めず、「サーバー処理」の説明欄に記載する:

| パターン | 例 | 説明 |
|----------|----|----- |
| `RedisUtil.getXxx()` | `paramMap.put("baseDate", RedisUtil.getKijyunbi())` | セッションから取得した基準日・会社コード等 |
| `setCommonParameters(paramMap)` | 共通処理での付加 | 基準日・ログインユーザー情報等を一括付加 |
| `request.getHeader(...)` / `SecurityContextHolder` | Spring Security由来 | 認証ユーザー情報 |

**フロントエンドのセッション連携パラメーター（送信はクライアントだが値はセッション由来）**:

```typescript
const param: any = {
  syainseq:  store.headerInfo.syainseq,   // ← Vuexストア（ログイン後セッション情報）
  accountid: store.headerInfo.accountid,  // ← 同上
  kaisyaCd:  store.headerInfo.kaisyaCd,   // ← 同上
  functionId: functionId.value,           // ← 画面固定値
  initFlag:  initFlag.value,              // ← 初期化フラグ（0 or 1）
  menuId:    route.params.menuId,         // ← URLパラメーター
  pageMode:  pageMode.value,              // ← 画面状態
}
```
`store.headerInfo.xxx` 由来のパラメーターは「クライアントが送信するが、セッション情報の引き渡し用」であることを設計書に明記する（`※ログインセッション由来`）。

**フロント↔バックのキー名不一致パターン（よくある落とし穴）**:
```java
// Javaのmapper.xml: #{empCode}
// Javaのservice: map.get("empCode")  ← JavaはcamelCase
// Vueのaxios: { emp_code: value }    ← Vueはsnake_case
// → フロントとバックでキー名が違う場合は両方を記録する
```

この場合のConfig記述:
```json
{
  "name": "社員コード（empCode / emp_code）",
  "value": "表示対象社員の社員コード"
}
```

## 8.4 コンテキスト管理（ファイルが多い場合の優先順位）

コンテキスト窓の制約がある場合、以下の優先順位でファイルを取捨選択する:

| 優先度 | ファイル種別 | 含めるべき部分 |
|--------|------------|--------------|
| 1（必須）| MyBatis XML | 当該機能のSQL定義全体 |
| 1（必須）| Mapper interface | メソッドシグネチャ + アノテーション |
| 2（重要）| Service実装クラス | 当該エンドポイントのメソッドのみ（全体不要）|
| 2（重要）| Vue composable/API service | APIコールの定義部分 |
| 3（補助）| Controller | エンドポイントメソッドのみ（クラス全体不要）|
| 3（補助）| Vue `<template>` | データバインディング部分（`v-if`, `:value`, `{{ }}`）|
| 4（省略可）| Vue `<style>` | 不要 |
| 4（省略可）| 共通BaseController | 暗黙的パラメーター付加が疑われる場合のみ |

**巨大ファイルの処理**: Serviceクラスが1000行以上ある場合、対象メソッドの前後50行だけを切り出してコンテキストに渡す。`analyze_code.py --extract-method` オプションで自動化できる。

## 8.5 不確実性の記録ルール

抽出できなかった・確信が持てないパラメーターは必ず記録し、設計書に残す。隠蔽しない。

```json
{
  "name": "ページ番号（pageNo）※要確認",
  "value": "ページング用パラメーター（Mapperで確認できず。Serviceのmap.get推測）"
}
```

notes配列での記録:
```json
"notes": [
  "※以下のパラメーターはコードから確認できなかった: authToken, sessionId",
  "※BaseControllerで共通パラメーターが付加される可能性がある（要確認）"
]
```

## 8.6 analyze_code.py の使い方

```bash
# 機能名で起点検索（Vueから辿る）
python scripts/analyze_code.py --project /path/to/project --feature "現職エリア"

# エンドポイントURLで起点検索（Controllerから辿る）
python scripts/analyze_code.py --project /path/to/project --url "/api/gensyokuarea/showBlock"

# ファイルを直接指定（ツールがファイル発見できない場合）
python scripts/analyze_code.py --files \
  path/to/SomeController.java \
  path/to/SomeService.java \
  path/to/SomMapper.xml \
  path/to/some_view.vue

# MyBatis XMLのみを直接指定（最高信頼度パラメーターだけ先に確認したい場合）
python scripts/analyze_code.py --mapper path/to/SomeMapper.xml

# 出力: analysis_result.json（パラメーター候補 + 信頼度 + 対象ファイル一覧）
# この出力をLLMに渡してConfig JSONに変換する
```

出力形式（実例：aplAprList機能）:
```json
{
  "feature": "aplAprList",
  "discovered_files": {
    "vue_components": ["front/aplaprlist/syoninichiran.vue"],
    "controllers":    ["back/aplaprlist/controller/AplAprListController.java"],
    "services":       ["back/aplaprlist/service/AplAprListService.java"],
    "mybatis_xml":    ["back/aplaprlist/mapper/AplAprListMapper.xml"]
  },
  "endpoints": [
    {
      "url": "/api/aplAprList/show",
      "method": "POST",
      "path_variables": [],
      "server_injected_params": ["baseDate", "kaisyaCd", "setCommonParameters"]
    },
    {
      "url": "/api/aplAprList/sideTree/show/{id}",
      "method": "POST",
      "path_variables": ["id"],
      "server_injected_params": []
    }
  ],
  "request_params": [
    {"name": "syainseq",   "source": "vue_direct",   "confidence": "MEDIUM", "note": "store.headerInfo由来（セッション）"},
    {"name": "accountid",  "source": "vue_direct",   "confidence": "MEDIUM", "note": "store.headerInfo由来（セッション）"},
    {"name": "functionId", "source": "mybatis_xml",  "confidence": "HIGH"},
    {"name": "initFlag",   "source": "mybatis_xml",  "confidence": "HIGH"},
    {"name": "menuId",     "source": "mybatis_xml",  "confidence": "HIGH"},
    {"name": "baseDate",   "source": "server_inject","confidence": "HIGH",   "note": "RedisUtil.getKijyunbi()"},
    {"name": "id",         "source": "path_variable","confidence": "HIGH",   "note": "@PathVariable"}
  ],
  "response_params": [
    {"name": "execTypeList",         "source": "vue_then",    "confidence": "MEDIUM"},
    {"name": "flowTypeList",         "source": "vue_then",    "confidence": "MEDIUM"},
    {"name": "syainApplicationsList","source": "mybatis_xml", "confidence": "HIGH"},
    {"name": "AprExectorList",       "source": "vue_then",    "confidence": "MEDIUM"}
  ],
  "uncertain": [
    "kaisyaCd（RedisUtil注入 vs Vueストア送信の二重定義 — どちらが優先されるか要確認）"
  ]
}
```

このJSONをそのまま第7章のConfig組立フックに渡すことで、人手なしにUI設計書のベース版を生成できる。

## 8.7 サーバー注入パラメーターの設計書への記載方法

サーバー側で自動付与されるパラメーターは**クライアントは送信しない**が、設計書に記載しなければ実装者が混乱する。以下の方針で記載する:

**内部-処理詳細シートへの記載例**:

| 区分 | パラメーター名 | 型 | 必須 | 説明 |
|------|------------|-----|------|------|
| REQ（クライアント）| initFlag | String | ○ | 初期化フラグ（0:通常, 1:初期化）|
| REQ（クライアント）| menuId | String | ○ | メニューID（URLパラメーターから取得）|
| REQ（パスパラメーター）| id | String | ○ | URLパス `/show/{id}` の `id` 部分 |
| REQ（サーバー付加）| baseDate | String | - | 基準日。Redisセッションから自動付加（`RedisUtil.getKijyunbi()`）|
| REQ（サーバー付加）| kaisyaCd | String | - | 会社コード。Redisセッションから自動付加 |
| RES | execTypeList | Array | - | 申請種別リスト（initFlag=1 の場合のみ返却）|
| RES | syainApplicationsList | Array | - | 申請一覧データ |

**条件付きレスポンスの記載**:

```
initFlag=1（初期化リクエスト）の場合のみ返却されるデータ:
  - execTypeList: 申請種別マスタリスト
  - flowTypeList: フロー種別マスタリスト
  - calendarItemStyleJson: カレンダースタイル設定JSON
  - AprExectorList: 承認者リスト
```

これをConfig JSONでは以下のように表現する:
```json
{
  "note": "initFlag=1（初期化）の場合、execTypeList, flowTypeList, calendarItemStyleJson, AprExectorList が追加で返却される"
}
```

## 8.8 実プロジェクトから学んだパターン集

実際のSIerプロジェクト（socia2026: Spring Boot + Vite + Vue3 + MyBatis）のコード解析から得られた典型的なパターン。

### パターン1: URL定数 + カスタムラッパー（querypost系）

```typescript
// syoninichiran.vue
import { querypost, querypostAbortable } from '@/common/api/syainkanriapi'

const API_URL = "/api/aplAprList"   // ← ベースURL定数

// 通常のPOST
querypost(API_URL + '/search', param).then(...)

// AbortController対応POST（長時間リクエストのキャンセル可能）
querypostAbortable(API_URL + '/show', param, signal).then(...)
```

→ `querypost`, `queryget`, `queryput`, `querypostAbortable` を axios の代替として検索対象に含める。

### パターン2: BaseController継承によるレスポンスラップ

```java
@RestController
public class AplAprListController extends BaseController {
    @PostMapping("/show")
    public ResponseEntity<Map<String, Object>> show(...) {
        ...
        return successResponse(result);  // BaseControllerのラッパー
    }
}
```

→ `successResponse()` の中身（result）がレスポンスボディ。`BaseController` がレスポンス構造（`code`, `message`, `data` 等）を追加する場合がある。

### パターン3: setCommonParameters() による共通付加

```java
public void someMethod(Map<String, Object> paramMap) {
    setCommonParameters(paramMap);  // ← これで baseDate, kaisyaCd 等が付加される
    List<Map<String, Object>> result = mapper.getSomeData(paramMap);
}
```

→ `setCommonParameters` の定義（通常 BaseService/AbstractService）を確認して、何が付加されるかを一度調べておく。同プロジェクト内では全エンドポイントに共通適用される。

### パターン4: キー名の大文字小文字ゆれ（実案件で確認済み）

同一プロジェクト内でも以下のようなゆれが存在する:
```
syainApplicationId  ↔  syainApplicationid   （最終文字のI大小文字）
templateId          ↔  templateid
approvalUserId      ↔  approvalUserid
```

→ Java側（MyBatis XML）と Vue側（res.data.xxx）で異なるケースがある。設計書には両方を「syainApplicationId (Java) / syainApplicationid (Vue)」のように併記する。

### パターン5: Lombok @RequiredArgsConstructor によるDI

```java
@Service
@RequiredArgsConstructor    // ← finalフィールドを自動でコンストラクタDI
public class AplAprListService {
    private final AplAprListMapper aplAprListMapper;   // ← Mapper自動注入
    private final RedisUtil redisUtil;
}
```

→ `@Autowired` がなくても、`final` フィールドを追う。`@RequiredArgsConstructor` がクラスにある場合は `final` フィールドが全てDIされている。
