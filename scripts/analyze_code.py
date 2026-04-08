#!/usr/bin/env python3
"""
analyze_code.py - Code2Excel ファイル発見・パラメーター抽出ツール
Spring Boot + Vue (Vite) / レガシーMapアーキテクチャ向け

Usage:
  # 機能名で起点検索（Vueから辿る）
  python scripts/analyze_code.py --project /path/to/project --feature "現職エリア"

  # エンドポイントURLで起点検索（Controllerから辿る）
  python scripts/analyze_code.py --project /path/to/project --url "/api/gensyokuarea/showBlock"

  # 特定ファイルを直接解析（ファイルが既にわかっている場合）
  python scripts/analyze_code.py --project /path/to/project --files file1.java file2.vue

  # Mapperのみを解析（最も信頼度が高い）
  python scripts/analyze_code.py --project /path/to/project --mapper GensyokuMapper.xml

Output:
  analysis_result.json を出力 — Config生成のLLMプロンプトへの入力として使用
"""

import re
import json
import os
import sys
import argparse
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from gitnexus_adapter import collect_scope_context
except Exception:
    collect_scope_context = None


# ============================================================
# 定数・設定
# ============================================================

# フロントエンドのソースディレクトリ候補（Vite + Vue）
FRONTEND_DIRS = [
    "src/views", "src/pages", "src/components", "src/features",
    "src/composables", "src/api", "src/services", "src/stores",
    "frontend/src", "web/src", "client/src",
]

# Spring Boot のソースディレクトリ候補
BACKEND_DIRS = [
    "src/main/java",
    "backend/src/main/java",
    "server/src/main/java",
]

# MyBatis XML のディレクトリ候補
MYBATIS_DIRS = [
    "src/main/resources/mapper",
    "src/main/resources/mybatis",
    "src/main/resources",
    "backend/src/main/resources/mapper",
]

# APIコールのパターン（Vue/TS）
API_CALL_PATTERNS = [
    # axios.post('/api/xxx', { key: val })
    r'(?:axios|api|http|client|request)\s*\.\s*(get|post|put|delete|patch)\s*\(\s*[\'"`]([^\'"` ]+)[\'"`]',
    # useXxx() composable 内の fetch
    r'fetch\s*\(\s*[\'"`]([^\'"` ]+)[\'"`]',
    # $http.post(url, params)
    r'\$http\s*\.\s*(get|post|put|delete)\s*\(\s*[\'"`]([^\'"` ]+)[\'"`]',
    # apiXxx.post(...)
    r'\w*[Aa]pi\w*\s*\.\s*(get|post|put|delete)\s*\(\s*[\'"`]([^\'"` ]+)[\'"`]',
    # querypost('/api/xxx', { ... }) — 日系SIerプロジェクトでよく使われるラッパー関数
    # querypost(API_URL + '/show', { ... }) — URL変数 + パス文字列の形式にも対応
    r'(?:querypost|queryget|queryput|querydelete|queryPostAbortable|querypostAbortable)\s*\(\s*(?:\w+\s*\+\s*)?[\'"`]([^\'"` ]+)[\'"`]',
    # request({ url: '/api/xxx', method: 'post', data: {...} }) 形式
    r'request\s*\(\s*\{[^}]{0,200}url\s*:\s*(?:\w+\s*\+\s*)?[\'"`]([^\'"` ]+)[\'"`]',
]

# querypost(API_URL + '/path', {...}) のURL変数展開パターン
# API_URL = "/api/aplAprList" のような定数定義を検出してURLを補完する
API_URL_VAR_PATTERN = re.compile(
    r'const\s+(\w*(?:URL|Url|url|BASE|Api)\w*)\s*=\s*[\'"`]([^\'"` ]+)[\'"`]'
)

STRING_LITERAL_VAR_PATTERN = re.compile(
    r'(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*[\'"`]([^\'"`]+)[\'"`]'
)

# Map.get() パターン（Java）
# 一般的な変数名: map, param, params, paramMap, inputData, req, request, data, result, body, form, info, dto, vo
# + JSONObject.getString("key"), optString("key") 等
MAP_GET_PATTERN = re.compile(
    r'(?:'
    r'(?:map|param|params|paramMap|inputMap|inputData|req|request|data|result|body|form|info|dto|vo|json|obj|args|condition|criteria)\w*'
    r')\s*\.(?:get|getOrDefault|getString|getInteger|getLong|getBoolean|getJSONObject|getJSONArray|optString|optInt)\s*\(\s*["\']([^"\']+)["\']\s*[,)]',
    re.IGNORECASE
)

# Map.put() パターン（Java）
MAP_PUT_PATTERN = re.compile(
    r'(?:'
    r'(?:map|result|resp|response|data|output|ret|returnMap|resultMap|resMap|body|json|obj)\w*'
    r')\s*\.(?:put|set|putIfAbsent)\s*\(\s*["\']([^"\']+)["\']\s*,',
    re.IGNORECASE
)

# MyBatis XML #{xxx} パターン
MYBATIS_PARAM_PATTERN = re.compile(r'#\{([^}]+)\}')

# Vue template バインディングパターン
VUE_BINDING_PATTERNS = [
    re.compile(r'v-model\s*=\s*["\'](?:\w+\.)*(\w+)["\']'),
    re.compile(r':\w+\s*=\s*["\'](?:\w+\.)*(\w+)["\']'),
    re.compile(r'\{\{\s*(?:\w+\.)*(\w+)\s*\}\}'),
    re.compile(r'v-if\s*=\s*["\'][^"\']*?\.(\w+)[^"\']*?["\']'),
]

# Spring Boot アノテーションパターン
CONTROLLER_ANNOTATION_PATTERN = re.compile(
    r'@(?:Rest)?Controller|@(?:Request|Get|Post|Put|Delete|Patch)Mapping\s*\(\s*(?:value\s*=\s*)?["\']([^"\']*)["\']'
)


# ============================================================
# ファイル発見
# ============================================================

def find_project_root(start_path: str) -> Path:
    """プロジェクトルートを推定（package.json or pom.xml の存在で判断）"""
    p = Path(start_path).resolve()
    for parent in [p] + list(p.parents):
        if (parent / "pom.xml").exists() or (parent / "package.json").exists():
            return parent
    return p


def find_vue_components(project_root: Path, feature_terms: List[str]) -> List[Path]:
    """機能名に関連するVueファイルを発見する"""
    results = []
    lower_terms = [t.lower() for t in feature_terms]

    for d in FRONTEND_DIRS:
        search_dir = project_root / d
        if not search_dir.exists():
            continue
        for f in search_dir.rglob("*.vue"):
            # ファイル名でマッチ
            stem = f.stem.lower()
            if any(t in stem for t in lower_terms):
                results.append(f)
                continue
            # ファイル内容でマッチ（最初の50行だけ確認）
            try:
                content = _read_head(f, 80)
                if any(t in content.lower() for t in lower_terms):
                    results.append(f)
            except Exception:
                pass

    return list(dict.fromkeys(results))  # 重複除去


def find_api_services(project_root: Path, feature_terms: List[str]) -> List[Path]:
    """APIサービス・composableファイルを発見する"""
    results = []
    lower_terms = [t.lower() for t in feature_terms]
    exts = ["*.ts", "*.js"]

    for d in FRONTEND_DIRS:
        search_dir = project_root / d
        if not search_dir.exists():
            continue
        for ext in exts:
            for f in search_dir.rglob(ext):
                stem = f.stem.lower()
                if any(t in stem for t in lower_terms):
                    results.append(f)
                    continue
                # api/composable ディレクトリ内のファイルも対象
                if any(x in str(f).lower() for x in ["api", "composable", "service", "store"]):
                    try:
                        content = _read_head(f, 50)
                        if any(t in content.lower() for t in lower_terms):
                            results.append(f)
                    except Exception:
                        pass

    return list(dict.fromkeys(results))


def find_controller_by_url(project_root: Path, url_path: str) -> List[Path]:
    """エンドポイントURLからSpring Boot Controllerを発見する"""
    normalized_target = _normalize_api_path_for_match(url_path)
    indexed = _build_controller_endpoint_index(str(project_root))
    return [Path(path) for path in indexed.get(normalized_target, [])]

@lru_cache(maxsize=32)
def _build_controller_endpoint_index(project_root_str: str) -> Dict[str, List[str]]:
    project_root = Path(project_root_str)
    search_roots = [project_root / d for d in BACKEND_DIRS if (project_root / d).exists()] or [project_root]
    index: Dict[str, List[str]] = {}
    for search_dir in search_roots:
        for controller_file in search_dir.rglob("*Controller*.java"):
            try:
                content = _read_file_text(controller_file)
                if "@RestController" not in content and "@Controller" not in content:
                    continue
                for endpoint in extract_controller_endpoints(controller_file):
                    normalized_path = _normalize_api_path_for_match(endpoint.get("path", ""))
                    index.setdefault(normalized_path, []).append(str(controller_file))
            except Exception:
                continue
    return {
        key: _merge_unique_strings(paths)
        for key, paths in index.items()
    }


def find_controller_by_feature(project_root: Path, feature_terms: List[str]) -> List[Path]:
    """機能名からSpring Boot Controllerを発見する"""
    results = []
    lower_terms = [t.lower() for t in feature_terms]

    for d in BACKEND_DIRS:
        search_dir = project_root / d
        if not search_dir.exists():
            continue
        for f in search_dir.rglob("*Controller*.java"):
            stem = f.stem.lower()
            if any(t in stem for t in lower_terms):
                results.append(f)
                continue
            try:
                content = _read_head(f, 30)
                if any(t in content.lower() for t in lower_terms):
                    results.append(f)
            except Exception:
                pass

    return list(dict.fromkeys(results))


def find_service_from_controller(project_root: Path, controller_file: Path) -> List[Path]:
    """Controllerのコードから呼び出しているServiceクラスを発見する"""
    results = []
    try:
        content = _read_file_text(controller_file)
    except Exception:
        return results

    # @Autowired や private XxxService xxxService; パターンからService名を抽出
    service_pattern = re.compile(r'(?:private|protected|public)?\s*(?:final\s+)?(\w+Service)\s+\w+')
    service_names = service_pattern.findall(content)

    # コンストラクタインジェクション / Lombok @RequiredArgsConstructor 対応
    # public XxxController(XxxService xxxService, YyyService yyyService)
    constructor_pattern = re.compile(r'(?:public\s+\w+\s*\(|@RequiredArgsConstructor).*?(\w+Service)', re.DOTALL)
    for match in constructor_pattern.finditer(content):
        svc = match.group(1)
        if svc not in service_names:
            service_names.append(svc)

    # importからパッケージパスを取得
    import_pattern = re.compile(r'import\s+([\w.]+\.(\w+Service))\s*;')
    for _, class_name in import_pattern.findall(content):
        if class_name not in service_names:
            service_names.append(class_name)

    search_roots = [project_root / d for d in BACKEND_DIRS if (project_root / d).exists()] or [project_root]
    for search_dir in search_roots:
        for svc_name in service_names:
            # ServiceImpl を優先検索
            for pattern in [f"*{svc_name}Impl.java", f"*{svc_name}.java"]:
                for f in search_dir.rglob(pattern):
                    results.append(f)

    return list(dict.fromkeys(results))


def find_mapper_from_service(project_root: Path, service_file: Path) -> Tuple[List[Path], List[Path]]:
    """Serviceファイルから呼び出しているMapperとMyBatis XMLを発見する"""
    java_mappers = []
    xml_mappers = []

    try:
        content = _read_file_text(service_file)
    except Exception:
        return java_mappers, xml_mappers

    # private XxxMapper xxxMapper; パターン
    mapper_pattern = re.compile(r'(?:private|protected|public)?\s*(\w+Mapper)\s+\w+')
    mapper_names = mapper_pattern.findall(content)

    import_pattern = re.compile(r'import\s+([\w.]+\.(\w+(?:Mapper|DAO|Dao)))\s*;')
    for _, class_name in import_pattern.findall(content):
        if class_name not in mapper_names:
            mapper_names.append(class_name)

    java_roots = [project_root / d for d in BACKEND_DIRS if (project_root / d).exists()] or [project_root]
    for search_dir in java_roots:
        for mapper_name in mapper_names:
            for f in search_dir.rglob(f"*{mapper_name}.java"):
                java_mappers.append(f)

    # MyBatis XML を Mapper名から検索
    xml_roots = [project_root / d for d in MYBATIS_DIRS if (project_root / d).exists()] or [project_root]
    for xml_dir in xml_roots:
        for mapper_name in mapper_names:
            for f in xml_dir.rglob(f"*{mapper_name}*.xml"):
                xml_mappers.append(f)

    return list(dict.fromkeys(java_mappers)), list(dict.fromkeys(xml_mappers))


# ============================================================
# パラメーター抽出
# ============================================================

def extract_api_calls_from_vue(file_path: Path) -> List[Dict]:
    """VueファイルからAPIコール情報を抽出する"""
    results = []
    try:
        content = _read_file_text(file_path)
    except Exception:
        return results

    # ファイル内のURL変数定義を収集（API_URL = "/api/xxx" など）
    url_vars: Dict[str, str] = {}
    for m in API_URL_VAR_PATTERN.finditer(content):
        url_vars[m.group(1)] = m.group(2)
    string_vars: Dict[str, str] = {}
    for m in STRING_LITERAL_VAR_PATTERN.finditer(content):
        string_vars[m.group(1)] = m.group(2)
    string_vars.update(url_vars)

    seen_urls = set()

    for pattern_str in API_CALL_PATTERNS:
        for m in re.finditer(pattern_str, content):
            groups = m.groups()
            if len(groups) == 2:
                method, url_part = groups[0], groups[1]
            else:
                method, url_part = "POST", groups[0]

            # URL変数を実際のパスで展開する
            # 例: API_URL + '/show' → '/api/aplAprList/show'
            full_url = url_part
            preceding = content[max(0, m.start()-80):m.start()]
            for var_name, var_val in url_vars.items():
                # querypost(API_URL + '/show', ...) の場合、APIパターンは '/show' を返す
                # 先行テキストにvar_nameが含まれていたら補完
                if var_name in preceding:
                    full_url = var_val + url_part
                    break
                # 直接 var_name + '/path' の形式を検出
                var_pattern = re.escape(var_name) + r'\s*\+\s*'
                if re.search(var_pattern, preceding + content[m.start():m.start()+30]):
                    full_url = var_val + url_part
                    break

            # 重複排除（同じURLの呼び出しは1件に）
            url_key = f"{method}:{full_url}"
            duplicate = url_key in seen_urls
            seen_urls.add(url_key)

            # リクエストボディのオブジェクトを探す（URLの直後 1000文字以内）
            pos = m.end()
            window = content[pos:pos + 1000]
            request_keys = _extract_object_keys(window)

            # インライン抽出できない場合→変数参照を追跡
            # 例: querypost(API_URL + '/show', param, ...) → param = { ... } を後方検索
            if not request_keys:
                request_keys = _trace_variable_param(content, pos)

            # .then((res) => { res.data.xxx }) からレスポンスキーを抽出
            response_keys = _extract_response_keys(content, m.start(), pos)

            results.append({
                "method": (method.upper() if method else "POST"),
                "url": full_url,
                "request_keys_vue": request_keys,
                "response_keys_vue": response_keys,
                "source_file": str(file_path),
                "confidence": "MEDIUM",
                "duplicate": duplicate,
            })

    results.extend(_extract_dynamic_api_calls(content, file_path, string_vars, seen_urls))

    # 重複フラグが立っているものを除外
    return [r for r in results if not r.get("duplicate")]


def _extract_dynamic_api_calls(content: str, file_path: Path, string_vars: Dict[str, str], seen_urls: set) -> List[Dict]:
    """querypost(baseUrl + api, param) のような動的URL組み立てを補足する。"""
    results = []
    dynamic_call_pattern = re.compile(
        r'(?P<fn>querypost|queryget|queryput|querydelete|queryPostAbortable|querypostAbortable)\s*\(\s*(?P<expr>[^,\r\n]+?)\s*,',
        re.IGNORECASE
    )

    for m in dynamic_call_pattern.finditer(content):
        full_url = _resolve_js_string_expression(
            m.group("expr"),
            string_vars,
            content=content,
            search_upto=m.start(),
        )
        if not full_url or "/" not in full_url:
            continue

        method = _infer_http_method_from_call(m.group("fn"))
        url_key = f"{method}:{full_url}"
        duplicate = url_key in seen_urls
        seen_urls.add(url_key)

        pos = m.end()
        request_keys = _trace_variable_param(content, pos)
        response_keys = _extract_response_keys(content, m.start(), pos)

        results.append({
            "method": method,
            "url": full_url,
            "request_keys_vue": request_keys,
            "response_keys_vue": response_keys,
            "source_file": str(file_path),
            "confidence": "MEDIUM",
            "duplicate": duplicate,
        })

    return results


def _resolve_js_string_expression(
    expr: str,
    string_vars: Dict[str, str],
    *,
    content: str = "",
    search_upto: int = 0,
) -> Optional[str]:
    """`foo + '/bar' + baz` のような単純な文字列式を解決する。"""
    parts = [part.strip() for part in expr.split("+")]
    if not parts:
        return None

    resolved = []
    for part in parts:
        if not part:
            continue
        literal_match = re.match(r'^[\'"`](.*)[\'"`]$', part)
        if literal_match:
            resolved.append(literal_match.group(1))
            continue
        scoped_value = _find_latest_string_assignment(content, part, search_upto)
        if scoped_value is not None:
            resolved.append(scoped_value)
            continue
        if part in string_vars:
            resolved.append(string_vars[part])
            continue
        return None

    full_url = "".join(resolved).strip()
    return full_url or None


def _find_latest_string_assignment(content: str, var_name: str, search_upto: int) -> Optional[str]:
    if not content or not var_name:
        return None
    window = content[:search_upto]
    pattern = re.compile(
        rf'(?<![\w.]){re.escape(var_name)}\s*=\s*[\'"`]([^\'"`]+)[\'"`]'
    )
    latest = None
    for match in pattern.finditer(window):
        latest = match.group(1)
    return latest


def _infer_http_method_from_call(fn_name: str) -> str:
    lowered = fn_name.lower()
    if "get" in lowered:
        return "GET"
    if "put" in lowered:
        return "PUT"
    if "delete" in lowered:
        return "DELETE"
    return "POST"


def _extract_object_keys(text: str) -> List[str]:
    """
    テキストの最初の {...} オブジェクトリテラルからキー名を抽出する。
    ネストされた {} は無視して最初のブロックのみを対象にする。
    """
    # 最初の { を探す
    brace_start = text.find('{')
    if brace_start < 0:
        return []
    # ネスト深さを数えて対応する } を見つける
    depth = 0
    end = brace_start
    for i, ch in enumerate(text[brace_start:], brace_start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                end = i
                break
    obj_body = text[brace_start + 1:end]
    # トップレベルのキーを抽出
    # - key: value
    # - shorthandKey,
    explicit_key_pattern = re.compile(r'(?:^|,|\n)\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*:', re.MULTILINE)
    shorthand_key_pattern = re.compile(r'(?:^|,|\n)\s*([a-zA-Z_$][a-zA-Z0-9_$]*)\s*(?=,|\n|$)', re.MULTILINE)
    keys = explicit_key_pattern.findall(obj_body)
    keys.extend(shorthand_key_pattern.findall(obj_body))
    # 除外リスト（一般的なオブジェクト操作キーワード）
    exclude = {'then', 'catch', 'finally', 'return', 'if', 'else', 'true', 'false', 'null'}
    ordered = []
    seen = set()
    for key in keys:
        if key in exclude or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def _trace_variable_param(content: str, api_call_end: int) -> List[str]:
    """
    querypost(url, param) の param が変数参照の場合、
    呼び出し位置の前方 2000文字以内で `const param = { ... }` または
    `const param: any = { ... }` の定義を探してキーを抽出する。
    """
    window_before = content[max(0, api_call_end - 2000):api_call_end]
    # 第2引数の変数名を特定（カンマの直後のword）
    after = content[api_call_end:api_call_end + 60]
    var_match = re.match(r'\s*,\s*([a-zA-Z_$][a-zA-Z0-9_$]*)', after)
    if not var_match:
        return []
    var_name = var_match.group(1)
    if var_name in ('null', 'undefined', 'true', 'false', 'signal', 'options', 'config'):
        return []

    # 変数定義を後方から検索
    # const param = { ... } / const param: any = { ... } / let param = { ... }
    defn_pattern = re.compile(
        r'(?:const|let|var)\s+' + re.escape(var_name) + r'\s*(?::\s*\w+[\w<>,\s]*)?\s*=\s*(\{)',
        re.MULTILINE
    )
    best_keys: List[str] = []
    for m in defn_pattern.finditer(window_before):
        obj_start = m.start(1)
        # window_before内でオブジェクト本体を取得
        obj_text = window_before[obj_start:]
        keys = _extract_object_keys(obj_text)
        if keys:
            best_keys = keys  # 最後に見つかったものを採用（最も近い定義）

    return best_keys


def _extract_response_keys(content: str, api_call_start: int, api_call_end: int) -> List[str]:
    keys = _extract_then_response_keys(content, api_call_end)
    if keys:
        return keys
    return _extract_await_response_keys(content, api_call_start, api_call_end)


def _extract_then_response_keys(content: str, api_call_end: int) -> List[str]:
    """
    querypost(...).then((res) => { ... res.data.xxx ... }) から
    レスポンスキー名を抽出する。
    """
    # .then( から始まる 1500文字を対象
    window = content[api_call_end:api_call_end + 1500]
    then_match = re.search(r'\.then\s*\(', window)
    if not then_match:
        return []

    then_body_start = api_call_end + then_match.end()
    then_body = content[then_body_start:then_body_start + 1200]

    keys: List[str] = []
    seen: set = set()

    # res.data.key / res?.data?.key / res.data["key"]
    patterns = [
        re.compile(r'res(?:\?)?\.data(?:\?)?\.([a-zA-Z_$][a-zA-Z0-9_$]*)'),
        re.compile(r'res(?:\?)?\.data(?:\?)?(?:\[)["\']([^"\']+)["\']'),
        # const { key1, key2 } = res.data
        re.compile(r'const\s*\{([^}]+)\}\s*=\s*res(?:\?)?\.data'),
    ]
    for pat in patterns[:2]:
        for m in pat.finditer(then_body):
            key = m.group(1)
            if key not in seen and key not in ('then', 'catch', 'finally', 'length'):
                seen.add(key)
                keys.append(key)

    # デストラクチャリング: const { execTypeList, flowTypeList } = res.data
    for m in patterns[2].finditer(then_body):
        for raw_key in m.group(1).split(','):
            key = raw_key.strip().split(':')[0].strip()  # { key: alias } の場合
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

    return keys


def _extract_await_response_keys(content: str, api_call_start: int, api_call_end: int) -> List[str]:
    """
    const res = await querypost(...); の後続処理から res.data.xxx を抽出する。
    """
    prefix = content[max(0, api_call_start - 120):api_call_start]
    match = re.search(
        r'([a-zA-Z_$][a-zA-Z0-9_$]*)\s*(?::\s*[^=]+)?=\s*await\s*$',
        prefix,
        re.MULTILINE
    )
    if not match:
        return []

    response_var = match.group(1)
    body = content[api_call_end:api_call_end + 1800]
    keys: List[str] = []
    seen: set = set()

    patterns = [
        re.compile(rf'{re.escape(response_var)}(?:\?)?\.data(?:\?)?\.([a-zA-Z_$][a-zA-Z0-9_$]*)'),
        re.compile(rf'{re.escape(response_var)}(?:\?)?\.data(?:\?)?(?:\[)["\']([^"\']+)["\']'),
        re.compile(rf'const\s*\{{([^}}]+)\}}\s*=\s*{re.escape(response_var)}(?:\?)?\.data'),
    ]
    for pat in patterns[:2]:
        for item in pat.finditer(body):
            key = item.group(1)
            if key not in seen and key not in ('then', 'catch', 'finally', 'length'):
                seen.add(key)
                keys.append(key)

    for item in patterns[2].finditer(body):
        for raw_key in item.group(1).split(','):
            key = raw_key.strip().split(':')[0].strip()
            if key and key not in seen:
                seen.add(key)
                keys.append(key)

    return keys


def _extract_brace_block(content: str, brace_start: int) -> Tuple[str, int]:
    if brace_start < 0 or brace_start >= len(content) or content[brace_start] != "{":
        return "", brace_start
    depth = 0
    for idx in range(brace_start, len(content)):
        ch = content[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[brace_start + 1:idx], idx
    return content[brace_start + 1:], len(content) - 1


def _split_java_arguments(args_text: str) -> List[str]:
    args = []
    buf = []
    paren_depth = 0
    brace_depth = 0
    bracket_depth = 0
    angle_depth = 0
    quote_char = None

    for ch in args_text:
        if quote_char:
            buf.append(ch)
            if ch == quote_char:
                quote_char = None
            continue

        if ch in ("'", '"'):
            quote_char = ch
            buf.append(ch)
            continue

        if ch == "(":
            paren_depth += 1
        elif ch == ")":
            paren_depth = max(0, paren_depth - 1)
        elif ch == "{":
            brace_depth += 1
        elif ch == "}":
            brace_depth = max(0, brace_depth - 1)
        elif ch == "[":
            bracket_depth += 1
        elif ch == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif ch == "<":
            angle_depth += 1
        elif ch == ">":
            angle_depth = max(0, angle_depth - 1)

        if ch == "," and paren_depth == 0 and brace_depth == 0 and bracket_depth == 0 and angle_depth == 0:
            token = "".join(buf).strip()
            if token:
                args.append(token)
            buf = []
            continue
        buf.append(ch)

    token = "".join(buf).strip()
    if token:
        args.append(token)
    return args


def _normalize_java_type(type_name: str) -> str:
    if not type_name:
        return ""
    cleaned = re.sub(r'@\w+(?:\([^)]*\))?\s*', "", type_name)
    cleaned = re.sub(r'\bfinal\b\s*', "", cleaned)
    cleaned = cleaned.strip()
    cleaned = cleaned.replace("...", "[]")
    return cleaned


def _parse_java_parameters(params_text: str) -> List[Dict[str, str]]:
    params = []
    for raw_param in _split_java_arguments(params_text):
        param = raw_param.strip()
        if not param:
            continue
        param = re.sub(r'@\w+(?:\([^)]*\))?\s*', "", param).strip()
        if not param:
            continue
        parts = param.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        param_type, param_name = parts[0].strip(), parts[1].strip()
        params.append({
            "type": _normalize_java_type(param_type),
            "name": param_name,
        })
    return params


def _extract_string_keys_for_var(body: str, var_name: str, methods: str) -> List[str]:
    if not body or not var_name:
        return []
    pattern = re.compile(
        rf'(?<![\w.]){re.escape(var_name)}\s*\.\s*(?:{methods})\s*\(\s*["\']([^"\']+)["\']',
        re.IGNORECASE,
    )
    return list(dict.fromkeys(match.group(1) for match in pattern.finditer(body)))


def _extract_local_map_put_keys(body: str, var_name: str) -> List[str]:
    return _extract_string_keys_for_var(body, var_name, r'put|putIfAbsent|set')


def _extract_local_map_get_keys(body: str, var_name: str) -> List[str]:
    return _extract_string_keys_for_var(
        body,
        var_name,
        r'get|getOrDefault|getString|getInteger|getLong|getBoolean|getJSONObject|getJSONArray|optString|optInt',
    )


def _dedupe_names(items: List[str]) -> List[str]:
    return list(dict.fromkeys(item for item in items if item))


@lru_cache(maxsize=256)
def _find_class_file(project_root_str: str, class_name: str) -> Optional[str]:
    project_root = Path(project_root_str)
    target = class_name.split(".")[-1].strip()
    if not target:
        return None
    candidates = list(project_root.rglob(f"{target}.java"))
    if not candidates:
        return None
    candidates.sort(key=lambda p: (len(str(p)), str(p)))
    return str(candidates[0])


@lru_cache(maxsize=256)
def _extract_java_class_fields(project_root_str: str, class_name: str) -> List[str]:
    class_file = _find_class_file(project_root_str, class_name)
    if not class_file:
        return []
    content = _read_file_text(Path(class_file))
    field_pattern = re.compile(
        r'private\s+(?!static\b)(?:final\s+)?[\w<>\[\], ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*;',
        re.MULTILINE,
    )
    fields = []
    for match in field_pattern.finditer(content):
        field = match.group(1)
        if field.isupper():
            continue
        fields.append(field)
    return _dedupe_names(fields)


def _find_java_method_definition(content: str, method_name: str) -> Optional[Dict[str, str]]:
    pattern = re.compile(
        rf'public\s+([^\{{;]+?)\s+{re.escape(method_name)}\s*\((.*?)\)\s*(?:throws\s+[^\{{]+)?\{{',
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return None
    return_type = _normalize_java_type(match.group(1).strip())
    params_text = match.group(2)
    body, _ = _extract_brace_block(content, match.end() - 1)
    return {
        "return_type": return_type,
        "params_text": params_text,
        "body": body,
    }


def _extract_list_item_put_keys(content: str, method_body: str, list_var_name: str, visited_helpers=None) -> List[str]:
    if not method_body or not list_var_name:
        return []

    visited_helpers = visited_helpers or set()
    keys: List[str] = []

    for_loop_pattern = re.compile(
        rf'for\s*\(\s*Map<[^>]+>\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*{re.escape(list_var_name)}\s*\)',
        re.DOTALL,
    )
    for loop_match in for_loop_pattern.finditer(method_body):
        row_var = loop_match.group(1)
        keys.extend(_extract_local_map_put_keys(method_body, row_var))

    add_pattern = re.compile(
        rf'(?<![\w.]){re.escape(list_var_name)}\s*\.\s*add\s*\(\s*([A-Za-z_][A-Za-z0-9_]*)\s*\)'
    )
    for add_match in add_pattern.finditer(method_body):
        item_var = add_match.group(1)
        keys.extend(_extract_local_map_put_keys(method_body, item_var))

    helper_call_pattern = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)', re.DOTALL)
    for helper_match in helper_call_pattern.finditer(method_body):
        helper_name = helper_match.group(1)
        if helper_name in visited_helpers:
            continue
        args = _split_java_arguments(helper_match.group(2))
        if not args or args[0].strip() != list_var_name:
            continue
        helper_def = _find_java_method_definition(content, helper_name)
        if not helper_def:
            continue
        helper_params = _parse_java_parameters(helper_def["params_text"])
        if not helper_params:
            continue
        helper_list_var = helper_params[0]["name"]
        keys.extend(
            _extract_list_item_put_keys(
                content,
                helper_def["body"],
                helper_list_var,
                visited_helpers | {helper_name},
            )
        )

    return _dedupe_names(keys)


@lru_cache(maxsize=256)
def _extract_mybatis_statement_result_keys(project_root_str: str, statement_id: str) -> List[str]:
    project_root = Path(project_root_str)
    if not statement_id:
        return []

    statement_pattern = re.compile(
        rf'<select\b[^>]*\bid=["\']{re.escape(statement_id)}["\'][^>]*>(.*?)</select>',
        re.IGNORECASE | re.DOTALL,
    )
    result_map_ref_pattern = re.compile(r'resultMap=["\']([^"\']+)["\']', re.IGNORECASE)
    result_map_pattern = re.compile(
        r'<resultMap\b[^>]*\bid=["\']([^"\']+)["\'][^>]*>(.*?)</resultMap>',
        re.IGNORECASE | re.DOTALL,
    )
    result_prop_pattern = re.compile(r'<(?:id|result)\b[^>]*property=["\']([^"\']+)["\']', re.IGNORECASE)
    alias_pattern = re.compile(r'\bas\s+([A-Za-z_][A-Za-z0-9_]*)', re.IGNORECASE)

    for xml_file in project_root.rglob("*.xml"):
        content = _read_file_text(xml_file)
        if statement_id not in content:
            continue
        statement_match = statement_pattern.search(content)
        if not statement_match:
            continue
        statement_body = statement_match.group(1)

        result_map_match = result_map_ref_pattern.search(statement_match.group(0))
        if result_map_match:
            result_map_id = result_map_match.group(1)
            for map_match in result_map_pattern.finditer(content):
                if map_match.group(1) != result_map_id:
                    continue
                props = _dedupe_names(result_prop_pattern.findall(map_match.group(2)))
                if props:
                    return props

        aliases = _dedupe_names(alias_pattern.findall(statement_body))
        if aliases:
            return aliases

    return []


@lru_cache(maxsize=256)
def _analyze_java_method_schema(project_root_str: str, class_name: str, method_name: str) -> Dict:
    class_file = _find_class_file(project_root_str, class_name)
    if not class_file:
        return {
            "class_file": None,
            "method_name": method_name,
            "request_keys": [],
            "response_keys_return": [],
            "response_keys_by_arg_index": {},
            "return_type": "",
        }

    content = _read_file_text(Path(class_file))
    method_def = _find_java_method_definition(content, method_name)
    if not method_def:
        return {
            "class_file": class_file,
            "method_name": method_name,
            "request_keys": [],
            "response_keys_return": [],
            "response_keys_by_arg_index": {},
            "return_type": "",
        }

    params = _parse_java_parameters(method_def["params_text"])
    body = method_def["body"]
    request_keys: List[str] = []
    response_keys_by_arg_index: Dict[int, List[str]] = {}

    for idx, param in enumerate(params):
        param_type = param["type"]
        param_name = param["name"]
        if "Map<" in param_type:
            request_keys.extend(_extract_local_map_get_keys(body, param_name))
            response_keys_by_arg_index[idx] = _extract_local_map_put_keys(body, param_name)

    response_keys_return: List[str] = []
    return_type = method_def["return_type"]
    return_type_base = re.sub(r'<.*>', "", return_type).split(".")[-1].strip()

    if return_type.startswith("List<"):
        inner_match = re.search(r'List\s*<\s*(.+)\s*>', return_type)
        inner_type = inner_match.group(1).strip() if inner_match else ""
        if "Map<" in inner_type:
            return_vars = [
                match.group(1)
                for match in re.finditer(r'return\s+([A-Za-z_][A-Za-z0-9_]*)\s*;', body)
            ]
            for return_var in return_vars:
                response_keys_return.extend(_extract_list_item_put_keys(content, body, return_var))
                mapper_call_pattern = re.compile(
                    rf'(?:List<Map<[^>]+>>\s+)?{re.escape(return_var)}\s*=\s*\w+Mapper\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(',
                    re.DOTALL,
                )
                for mapper_match in mapper_call_pattern.finditer(body):
                    response_keys_return.extend(
                        _extract_mybatis_statement_result_keys(project_root_str, mapper_match.group(1))
                    )
        else:
            inner_type_base = re.sub(r'<.*>', "", inner_type).split(".")[-1].strip()
            response_keys_return.extend(_extract_java_class_fields(project_root_str, inner_type_base))
    elif "Map<" in return_type:
        return_vars = [
            match.group(1)
            for match in re.finditer(r'return\s+([A-Za-z_][A-Za-z0-9_]*)\s*;', body)
        ]
        for return_var in return_vars:
            response_keys_return.extend(_extract_local_map_put_keys(body, return_var))
    elif return_type_base and return_type_base not in {"void", "ResponseEntity"}:
        response_keys_return.extend(_extract_java_class_fields(project_root_str, return_type_base))

    return {
        "class_file": class_file,
        "method_name": method_name,
        "request_keys": _dedupe_names(request_keys),
        "response_keys_return": _dedupe_names(response_keys_return),
        "response_keys_by_arg_index": {
            idx: _dedupe_names(keys)
            for idx, keys in response_keys_by_arg_index.items()
            if keys
        },
        "return_type": return_type,
    }


def _normalize_api_path_for_match(path: str) -> str:
    normalized = re.sub(r'\{[^}]+\}', "", path or "")
    normalized = re.sub(r'/+', "/", normalized).rstrip("/")
    return normalized or "/"


def _merge_backend_endpoint_details(api_calls: List[Dict], endpoints: List[Dict]) -> List[Dict]:
    merged = [dict(call) for call in api_calls]

    for endpoint in endpoints:
        endpoint_path = endpoint.get("path", "")
        endpoint_method = (endpoint.get("method") or "POST").upper()
        normalized_endpoint = _normalize_api_path_for_match(endpoint_path)
        target = None

        for call in merged:
            call_method = (call.get("method") or "POST").upper()
            if call_method != endpoint_method:
                continue
            normalized_call = _normalize_api_path_for_match(call.get("url", ""))
            if normalized_call == normalized_endpoint:
                target = call
                break

        if target is None:
            target = {
                "method": endpoint_method,
                "url": endpoint_path,
                "request_keys_vue": [],
                "response_keys_vue": [],
                "source_file": endpoint.get("source_file"),
                "confidence": "HIGH",
            }
            merged.append(target)

        target["request_keys_backend"] = endpoint.get("request_keys_backend", [])
        target["response_keys_backend"] = endpoint.get("response_keys_backend", [])
        if endpoint.get("backend_service_method"):
            target["backend_service_method"] = endpoint["backend_service_method"]
        if endpoint.get("backend_service_file"):
            target["backend_service_file"] = endpoint["backend_service_file"]

    return merged


def extract_controller_endpoints(file_path: Path, project_root: Optional[Path] = None) -> List[Dict]:
    """
    Spring Boot ControllerファイルからAPIエンドポイント一覧を抽出する。
    - @RequestMapping(value="/xxx", method=POST) → endpoint情報
    - @PathVariable String id → パスパラメーター
    - paramMap.put("key", RedisUtil.xxx()) → サーバー側注入パラメーター（除外候補）
    """
    endpoints = []
    try:
        content = _read_file_text(file_path)
    except Exception:
        return endpoints

    def _extract_mapping_path(args_text: str) -> str:
        named_match = re.search(r'(?:value|path)\s*=\s*["\']([^"\']+)["\']', args_text)
        if named_match:
            return named_match.group(1)
        literal_match = re.search(r'["\']([^"\']+)["\']', args_text)
        if literal_match:
            return literal_match.group(1)
        return ""

    def _extract_http_method(annotation_name: str, args_text: str) -> str:
        if annotation_name == "GetMapping":
            return "GET"
        if annotation_name == "PostMapping":
            return "POST"
        if annotation_name == "PutMapping":
            return "PUT"
        if annotation_name == "DeleteMapping":
            return "DELETE"
        if annotation_name == "PatchMapping":
            return "PATCH"
        explicit_method = re.search(r'RequestMethod\.(GET|POST|PUT|DELETE|PATCH)', args_text)
        if explicit_method:
            return explicit_method.group(1)
        return "POST"

    # クラスレベルのベースパス取得
    base_path = ""
    class_mapping = re.search(r'@RequestMapping\s*\(\s*(.*?)\)', content, re.DOTALL)
    if class_mapping:
        base_path = _extract_mapping_path(class_mapping.group(1)).rstrip('/')

    # メソッドレベルのマッピング
    method_pattern = re.compile(
        r'@(?P<annotation>RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)'
        r'\s*\((?P<args>[^)]*)\)\s*'
        r'(?:@\w+(?:\([^)]*\))?\s*)*'
        r'public\s+(?!class\b)(?P<return_type>[^\{;]+?)\s+(?P<method_name>[A-Za-z_][A-Za-z0-9_]*)\s*'
        r'\((?P<params>.*?)\)\s*(?:throws\s+[^\{]+)?\{',
        re.DOTALL,
    )
    # パスパラメーター (@PathVariable)
    path_var_pattern = re.compile(r'@PathVariable\s+\w+\s+(\w+)')
    # サーバー側注入パターン (Redis / setCommonParameters)
    server_inject_pattern = re.compile(
        r'paramMap\.put\s*\(\s*["\']([^"\']+)["\']\s*,\s*(?:RedisUtil|SociaUtil\.cvt|baseDate|getKijyunbi)'
    )

    service_field_map = {
        match.group(2): match.group(1)
        for match in re.finditer(
            r'^\s*(?:private|protected|public)?\s*([A-Za-z_][A-Za-z0-9_]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*;',
            content,
            re.MULTILINE,
        )
    }

    for m in method_pattern.finditer(content):
        annotation_name = m.group("annotation")
        annotation_args = m.group("args")
        path = base_path + _extract_mapping_path(annotation_args)
        http_method = _extract_http_method(annotation_name, annotation_args)
        return_type = _normalize_java_type(m.group("return_type").strip())
        method_name = m.group("method_name")
        params_text = m.group("params")
        body_start = m.end() - 1
        body, _ = _extract_brace_block(content, body_start)

        path_vars = path_var_pattern.findall(params_text)
        server_injected = server_inject_pattern.findall(body)

        endpoint = {
            "path": path,
            "method": http_method,
            "method_name": method_name,
            "return_type": return_type,
            "path_variables": path_vars,
            "server_injected_params": server_injected,
            "source_file": str(file_path),
        }

        if project_root:
            request_keys_backend: List[str] = list(path_vars)
            response_keys_backend: List[str] = []
            request_body_param = None
            for raw_param in _split_java_arguments(params_text):
                if "@RequestBody" not in raw_param:
                    continue
                cleaned_param = re.sub(r'@\w+(?:\([^)]*\))?\s*', "", raw_param).strip()
                parts = cleaned_param.rsplit(" ", 1)
                if len(parts) != 2:
                    continue
                request_body_param = {
                    "type": _normalize_java_type(parts[0]),
                    "name": parts[1].strip(),
                }
                break

            success_expr_match = re.search(r'return\s+super\.successResponse\s*\((.*?)\)\s*;', body, re.DOTALL)
            success_expr = " ".join(success_expr_match.group(1).split()) if success_expr_match else None

            if request_body_param:
                request_body_type = request_body_param["type"]
                request_body_name = request_body_param["name"]
                endpoint["request_body_type"] = request_body_type
                endpoint["request_body_name"] = request_body_name
                if "Map<" in request_body_type:
                    request_keys_backend.extend(_extract_local_map_get_keys(body, request_body_name))
                else:
                    request_keys_backend.extend(
                        _extract_java_class_fields(str(project_root), request_body_type.split(".")[-1])
                    )

            if success_expr and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', success_expr):
                response_keys_backend.extend(_extract_local_map_put_keys(body, success_expr))

            handled_service_calls = set()
            if success_expr:
                direct_service_match = re.fullmatch(
                    r'([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*)\)',
                    success_expr,
                    re.DOTALL,
                )
                if direct_service_match:
                    field_name = direct_service_match.group(1)
                    service_method = direct_service_match.group(2)
                    args_text = direct_service_match.group(3)
                    if field_name in service_field_map:
                        service_class = service_field_map[field_name]
                        service_schema = _analyze_java_method_schema(str(project_root), service_class, service_method)
                        request_keys_backend.extend(service_schema.get("request_keys", []))
                        response_keys_backend.extend(service_schema.get("response_keys_return", []))
                        endpoint["backend_service_method"] = service_method
                        if service_schema.get("class_file"):
                            endpoint["backend_service_file"] = service_schema["class_file"]
                        handled_service_calls.add((field_name, service_method, " ".join(args_text.split())))

            service_call_pattern = re.compile(
                r'([A-Za-z_][A-Za-z0-9_]*)\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)',
                re.DOTALL,
            )
            for call_match in service_call_pattern.finditer(body):
                field_name = call_match.group(1)
                service_method = call_match.group(2)
                args_text = call_match.group(3)
                if field_name not in service_field_map:
                    continue
                call_key = (field_name, service_method, " ".join(args_text.split()))
                if call_key in handled_service_calls:
                    continue

                service_class = service_field_map[field_name]
                service_schema = _analyze_java_method_schema(str(project_root), service_class, service_method)
                request_keys_backend.extend(service_schema.get("request_keys", []))

                args = _split_java_arguments(args_text)
                if success_expr and f"{field_name}.{service_method}(" in success_expr:
                    response_keys_backend.extend(service_schema.get("response_keys_return", []))

                if success_expr and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', success_expr):
                    for idx, arg in enumerate(args):
                        if arg.strip() != success_expr:
                            continue
                        response_keys_backend.extend(
                            service_schema.get("response_keys_by_arg_index", {}).get(idx, [])
                        )

                endpoint["backend_service_method"] = service_method
                if service_schema.get("class_file"):
                    endpoint["backend_service_file"] = service_schema["class_file"]

            endpoint["request_keys_backend"] = _dedupe_names(request_keys_backend)
            endpoint["response_keys_backend"] = _dedupe_names(response_keys_backend)

        endpoints.append(endpoint)

    return endpoints


def extract_map_keys_from_java(file_path: Path) -> Dict[str, List[Dict]]:
    """JavaファイルからMap.get/putのキー名を抽出する"""
    request_params = []
    response_params = []

    try:
        content = _read_file_text(file_path)
    except Exception:
        return {"request": [], "response": []}

    # Map.get() → リクエストパラメーター
    for m in MAP_GET_PATTERN.finditer(content):
        key = m.group(1)
        # 周辺コード（前後20文字）を取得してコンテキスト情報として追加
        start = max(0, m.start() - 60)
        end = min(len(content), m.end() + 60)
        context = content[start:end].replace('\n', ' ').strip()
        request_params.append({
            "name": key,
            "source": f"java_map_get:{file_path.name}",
            "confidence": "HIGH" if "Mapper" not in file_path.name else "MEDIUM",
            "context_hint": context,
        })

    # Map.put() → レスポンスパラメーター
    for m in MAP_PUT_PATTERN.finditer(content):
        key = m.group(1)
        start = max(0, m.start() - 60)
        end = min(len(content), m.end() + 60)
        context = content[start:end].replace('\n', ' ').strip()
        response_params.append({
            "name": key,
            "source": f"java_map_put:{file_path.name}",
            "confidence": "HIGH",
            "context_hint": context,
        })

    return {
        "request": _deduplicate_params(request_params),
        "response": _deduplicate_params(response_params),
    }


def extract_mybatis_params(file_path: Path) -> Dict[str, List[Dict]]:
    """MyBatis XMLから#{xxx}パラメーターを抽出する（最高信頼度）"""
    input_params = []
    result_mappings = []

    try:
        content = _read_file_text(file_path)
    except Exception:
        return {"input": [], "result": []}

    # #{paramName} → SQLの入力パラメーター
    for m in MYBATIS_PARAM_PATTERN.finditer(content):
        param_raw = m.group(1)
        # #{param.field} の場合は field を取得
        param_name = param_raw.split('.')[-1].strip()
        input_params.append({
            "name": param_name,
            "source": f"mybatis_xml:{file_path.name}",
            "confidence": "HIGH",
            "context_hint": f"MyBatis SQL parameter: #{{{param_raw}}}",
        })

    # <result property="xxx" column="yyy"/> → レスポンスフィールド
    result_pattern = re.compile(r'<result\s+[^>]*property=["\'](\w+)["\'][^>]*>')
    for m in result_pattern.finditer(content):
        result_mappings.append({
            "name": m.group(1),
            "source": f"mybatis_resultmap:{file_path.name}",
            "confidence": "HIGH",
            "context_hint": "MyBatis ResultMap property",
        })

    return {
        "input": _deduplicate_params(input_params),
        "result": _deduplicate_params(result_mappings),
    }


def extract_vue_template_bindings(file_path: Path) -> List[Dict]:
    """Vueのtemplateからデータバインディングのキーを抽出する"""
    results = []
    try:
        content = _read_file_text(file_path)
    except Exception:
        return results

    # <template>セクションのみを対象にする
    template_match = re.search(r'<template>(.*?)</template>', content, re.DOTALL)
    template_content = template_match.group(1) if template_match else content

    seen = set()
    for pattern in VUE_BINDING_PATTERNS:
        for m in pattern.finditer(template_content):
            key = m.group(1)
            # 一般的な変数名・HTMLキーワードを除外
            if key in seen or key in ("class", "style", "key", "ref", "id", "true", "false", "null"):
                continue
            seen.add(key)
            results.append({
                "name": key,
                "source": f"vue_template:{file_path.name}",
                "confidence": "MEDIUM",
                "context_hint": "Vue template binding",
            })

    return results


# ============================================================
# メイン分析処理
# ============================================================

def analyze_feature(project_root_str: str, feature: str) -> Dict:
    """機能名を起点にファイル発見・パラメーター抽出を実行する"""
    project_root = Path(project_root_str).resolve()

    # 機能名をローマ字・英字・日本語に分解して検索ワードを作る
    feature_terms = _expand_feature_terms(feature)
    print(f"[analyze] 機能名: {feature}")
    print(f"[analyze] 検索ワード: {feature_terms}")

    result = {
        "feature": feature,
        "project_root": str(project_root),
        "discovered_files": {
            "vue_components": [],
            "api_services": [],
            "controllers": [],
            "services": [],
            "mappers": [],
            "mybatis_xml": [],
        },
        "api_calls": [],
        "request_params": [],
        "response_params": [],
        "uncertain": [],
        "llm_context_files": [],  # LLMに渡すべきファイルの優先リスト
    }

    # Phase 1: ファイル発見
    vue_files = find_vue_components(project_root, feature_terms)
    api_files = find_api_services(project_root, feature_terms)
    controllers = find_controller_by_feature(project_root, feature_terms)

    result["discovered_files"]["vue_components"] = [str(f) for f in vue_files]
    result["discovered_files"]["api_services"] = [str(f) for f in api_files]
    result["discovered_files"]["controllers"] = [str(f) for f in controllers]

    print(f"[analyze] Vue: {len(vue_files)}件, APIサービス: {len(api_files)}件, Controller: {len(controllers)}件")

    # Service → Mapper → XML と連鎖追跡
    all_services = []
    all_mappers = []
    all_xmls = []

    for ctrl in controllers:
        svcs = find_service_from_controller(project_root, ctrl)
        all_services.extend(svcs)

    for svc in all_services:
        mappers, xmls = find_mapper_from_service(project_root, svc)
        all_mappers.extend(mappers)
        all_xmls.extend(xmls)

    result["discovered_files"]["services"] = [str(f) for f in all_services]
    result["discovered_files"]["mappers"] = [str(f) for f in all_mappers]
    result["discovered_files"]["mybatis_xml"] = [str(f) for f in all_xmls]

    print(f"[analyze] Service: {len(all_services)}件, Mapper: {len(all_mappers)}件, XML: {len(all_xmls)}件")

    # Phase 2: パラメーター抽出
    all_request_params = []
    all_response_params = []

    # MyBatis XML（最高信頼度）
    for xml_f in all_xmls:
        extracted = extract_mybatis_params(xml_f)
        all_request_params.extend(extracted["input"])
        all_response_params.extend(extracted["result"])

    # Java Service/Mapper
    for java_f in all_services + all_mappers + controllers:
        extracted = extract_map_keys_from_java(java_f)
        all_request_params.extend(extracted["request"])
        all_response_params.extend(extracted["response"])

    # Vue APIコール
    for vue_f in vue_files + api_files:
        api_calls = extract_api_calls_from_vue(vue_f)
        result["api_calls"].extend(api_calls)
        # Vueのリクエストキーも追加（信頼度MEDIUM）
        for call in api_calls:
            for key in call.get("request_keys_vue", []):
                all_request_params.append({
                    "name": key,
                    "source": f"vue_axios:{Path(call['source_file']).name}",
                    "confidence": "MEDIUM",
                    "context_hint": f"Vue axios request key for {call['url']}",
                })

    # Vue templateバインディング（レスポンスパラメーターとして収集）
    for vue_f in vue_files:
        bindings = extract_vue_template_bindings(vue_f)
        all_response_params.extend(bindings)

    # 重複除去・マージ（MyBatis HIGH が同名のMEDIUMを上書き）
    result["request_params"] = _merge_params(all_request_params)
    result["response_params"] = _merge_params(all_response_params)

    # LLMに渡す優先ファイルリストを構築
    result["llm_context_files"] = _build_llm_context_priority(
        all_xmls, all_mappers, all_services, controllers,
        api_files, vue_files
    )

    # 未確認パラメーターのフラグ
    unknown_req = [p["name"] for p in result["request_params"] if p["confidence"] == "UNKNOWN"]
    unknown_res = [p["name"] for p in result["response_params"] if p["confidence"] == "UNKNOWN"]
    if unknown_req:
        result["uncertain"].append(f"リクエストパラメーター要確認: {', '.join(unknown_req)}")
    if unknown_res:
        result["uncertain"].append(f"レスポンスパラメーター要確認: {', '.join(unknown_res)}")

    # BaseControllerが存在する場合の警告
    base_controllers = list(project_root.rglob("*BaseController*.java"))
    if base_controllers:
        result["uncertain"].append(
            f"BaseControllerが存在します（{base_controllers[0].name}）。"
            "共通パラメーターが暗黙的に付加されている可能性があります。"
        )

    return result


def analyze_by_url(project_root_str: str, url_path: str) -> Dict:
    """エンドポイントURLを起点に分析する"""
    project_root = Path(project_root_str).resolve()

    # URLからfeature_termsを推測
    parts = [p for p in url_path.split("/") if p and p not in ("api", "v1", "v2")]
    feature_terms = parts

    print(f"[analyze] URL: {url_path}")
    print(f"[analyze] URL推定ワード: {feature_terms}")

    result = {
        "feature": url_path,
        "project_root": str(project_root),
        "discovered_files": {
            "vue_components": [],
            "api_services": [],
            "controllers": [],
            "services": [],
            "mappers": [],
            "mybatis_xml": [],
        },
        "api_calls": [],
        "request_params": [],
        "response_params": [],
        "uncertain": [],
        "llm_context_files": [],
    }

    controllers = find_controller_by_url(project_root, url_path)
    result["discovered_files"]["controllers"] = [str(f) for f in controllers]

    if not controllers:
        print(f"[warn] Controllerが見つかりませんでした。URLパターン: {url_path}")
        result["uncertain"].append(f"Controller未発見: {url_path}")

    all_services = []
    all_mappers = []
    all_xmls = []

    for ctrl in controllers:
        svcs = find_service_from_controller(project_root, ctrl)
        all_services.extend(svcs)

    for svc in all_services:
        mappers, xmls = find_mapper_from_service(project_root, svc)
        all_mappers.extend(mappers)
        all_xmls.extend(xmls)

    result["discovered_files"]["services"] = [str(f) for f in all_services]
    result["discovered_files"]["mappers"] = [str(f) for f in all_mappers]
    result["discovered_files"]["mybatis_xml"] = [str(f) for f in all_xmls]

    # パラメーター抽出（feature分析と同様）
    all_request_params = []
    all_response_params = []

    for xml_f in all_xmls:
        extracted = extract_mybatis_params(xml_f)
        all_request_params.extend(extracted["input"])
        all_response_params.extend(extracted["result"])

    for java_f in all_services + all_mappers + controllers:
        extracted = extract_map_keys_from_java(java_f)
        all_request_params.extend(extracted["request"])
        all_response_params.extend(extracted["response"])

    result["request_params"] = _merge_params(all_request_params)
    result["response_params"] = _merge_params(all_response_params)

    result["llm_context_files"] = _build_llm_context_priority(
        all_xmls, all_mappers, all_services, controllers, [], []
    )

    return result


def analyze_direct_files(project_root_str: str, file_paths: List[str], mapper_path: Optional[str]) -> Dict:
    """指定ファイルを直接解析する（ファイルが既にわかっている場合）"""
    project_root = Path(project_root_str).resolve()

    result = {
        "feature": "direct_files",
        "project_root": str(project_root),
        "discovered_files": {
            "vue_components": [],
            "api_services": [],
            "controllers": [],
            "services": [],
            "mappers": [],
            "mybatis_xml": [],
        },
        "api_calls": [],
        "request_params": [],
        "response_params": [],
        "uncertain": [],
        "llm_context_files": [],
    }

    all_request_params = []
    all_response_params = []

    # --mapper: MyBatis XMLを直接解析
    if mapper_path:
        xml_f = Path(mapper_path)
        if not xml_f.is_absolute():
            xml_f = project_root / xml_f
        if xml_f.exists():
            result["discovered_files"]["mybatis_xml"].append(str(xml_f))
            extracted = extract_mybatis_params(xml_f)
            all_request_params.extend(extracted["input"])
            all_response_params.extend(extracted["result"])
            print(f"[analyze] MyBatis XML: {xml_f.name} → 入力{len(extracted['input'])}件, 出力{len(extracted['result'])}件")
        else:
            print(f"[warn] MyBatis XMLが見つかりません: {mapper_path}")

    # --files: 各ファイルを拡張子に応じて解析
    for fp in file_paths:
        f = Path(fp)
        if not f.is_absolute():
            f = project_root / f
        if not f.exists():
            print(f"[warn] ファイルが見つかりません: {fp}")
            continue

        ext = f.suffix.lower()
        if ext == ".vue":
            result["discovered_files"]["vue_components"].append(str(f))
            api_calls = extract_api_calls_from_vue(f)
            result["api_calls"].extend(api_calls)
            for call in api_calls:
                for key in call.get("request_keys_vue", []):
                    all_request_params.append({
                        "name": key,
                        "source": f"vue_querypost:{f.name}",
                        "confidence": "MEDIUM",
                        "context_hint": f"Vue request key for {call['url']}",
                    })
                # .then()から取得したレスポンスキーを追加
                for key in call.get("response_keys_vue", []):
                    all_response_params.append({
                        "name": key,
                        "source": f"vue_then:{f.name}",
                        "confidence": "MEDIUM",
                        "context_hint": f"Vue .then() response key for {call['url']}",
                    })
            bindings = extract_vue_template_bindings(f)
            all_response_params.extend(bindings)

        elif ext in (".ts", ".js"):
            result["discovered_files"]["api_services"].append(str(f))
            api_calls = extract_api_calls_from_vue(f)
            result["api_calls"].extend(api_calls)

        elif ext == ".java":
            # Javaファイルの種類を推定
            stem = f.stem
            if "Controller" in stem:
                result["discovered_files"]["controllers"].append(str(f))
                # Controllerからエンドポイント一覧とパスパラメーターを抽出
                endpoints = extract_controller_endpoints(f, project_root)
                if endpoints:
                    result.setdefault("endpoints", []).extend(endpoints)
                    # PathVariableはリクエストパラメーターとして追加
                    for ep in endpoints:
                        for pv in ep.get("path_variables", []):
                            all_request_params.append({
                                "name": pv,
                                "source": f"path_variable:{f.name}",
                                "confidence": "HIGH",
                                "context_hint": f"URL path variable in {ep['method']} {ep['path']}",
                            })
                        for key in ep.get("request_keys_backend", []):
                            all_request_params.append({
                                "name": key,
                                "source": f"backend_endpoint_request:{f.name}",
                                "confidence": "HIGH",
                                "context_hint": f"Backend request key for {ep['method']} {ep['path']}",
                            })
                        for key in ep.get("response_keys_backend", []):
                            all_response_params.append({
                                "name": key,
                                "source": f"backend_endpoint_response:{f.name}",
                                "confidence": "HIGH",
                                "context_hint": f"Backend response key for {ep['method']} {ep['path']}",
                            })
            elif "Service" in stem:
                result["discovered_files"]["services"].append(str(f))
            elif "Mapper" in stem or "DAO" in stem or "Dao" in stem:
                result["discovered_files"]["mappers"].append(str(f))

            extracted = extract_map_keys_from_java(f)
            all_request_params.extend(extracted["request"])
            all_response_params.extend(extracted["response"])

        elif ext == ".xml":
            result["discovered_files"]["mybatis_xml"].append(str(f))
            extracted = extract_mybatis_params(f)
            all_request_params.extend(extracted["input"])
            all_response_params.extend(extracted["result"])

        print(f"[analyze] {ext}: {f.name}")

    if result.get("endpoints"):
        result["api_calls"] = _merge_backend_endpoint_details(result["api_calls"], result["endpoints"])

    result["request_params"] = _merge_params(all_request_params)
    result["response_params"] = _merge_params(all_response_params)

    # LLMコンテキスト
    all_files = [Path(p) for cat in result["discovered_files"].values() for p in cat]
    result["llm_context_files"] = [{"priority": 1, "type": "direct", "path": str(f)} for f in all_files]

    return result


def analyze_scope_with_gitnexus(
    *,
    project_root_str: str,
    front_root: str,
    back_root: str,
    feature: Optional[str],
    url_path: Optional[str],
    scope_files: List[str],
    mapper_path: Optional[str],
    engine: str,
    front_repo_name: str,
    back_repo_name: str,
    gitnexus_limit: int,
) -> Dict:
    """gitnexus を前段に使い、必要なら direct-files 解析で補完する。"""
    if collect_scope_context is None:
        raise RuntimeError("gitnexus_adapter.py の読み込みに失敗しました。")

    graph_result = collect_scope_context(
        front_root=front_root,
        back_root=back_root,
        feature=feature,
        url=url_path,
        scope_files=scope_files,
        front_repo_name=front_repo_name,
        back_repo_name=back_repo_name,
        limit=gitnexus_limit,
    )

    if engine == "gitnexus":
        return graph_result

    discovered_file_groups = graph_result.get("discovered_files", {})
    candidate_files = _merge_unique_strings(
        discovered_file_groups.get("vue_components", []),
        discovered_file_groups.get("api_services", []),
        discovered_file_groups.get("controllers", []),
        discovered_file_groups.get("services", []),
        discovered_file_groups.get("mappers", []),
        discovered_file_groups.get("mybatis_xml", []),
        scope_files,
    )

    if not candidate_files and not mapper_path:
        graph_result["uncertain"].append(
            "scope 内の候補ファイルが不足しているため、hybrid 補完を実行できませんでした。"
        )
        return graph_result

    heuristic_result = analyze_direct_files(project_root_str, candidate_files, mapper_path)
    backend_augmented = _augment_backend_from_api_calls(Path(back_root), heuristic_result.get("api_calls", []))
    heuristic_result["api_calls"] = backend_augmented.get("api_calls", heuristic_result.get("api_calls", []))
    heuristic_result["discovered_files"] = _merge_discovered_files(
        heuristic_result.get("discovered_files", {}),
        backend_augmented.get("discovered_files", {}),
    )
    heuristic_result["request_params"] = _merge_params(
        heuristic_result.get("request_params", []) + backend_augmented.get("request_params", [])
    )
    heuristic_result["response_params"] = _merge_params(
        heuristic_result.get("response_params", []) + backend_augmented.get("response_params", [])
    )
    if backend_augmented.get("endpoints"):
        heuristic_result["endpoints"] = backend_augmented["endpoints"]
    return _merge_analysis_results(graph_result, heuristic_result)


# ============================================================
# ユーティリティ
# ============================================================

def _read_file_text(file_path: Path) -> str:
    """ファイル全体を読む（UTF-8優先、失敗時にShift-JIS/CP932フォールバック）"""
    for enc in ("utf-8", "cp932", "euc-jp", "iso-8859-1"):
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            return ""
    # 最終手段: errors=ignore でバイナリ安全に読む
    return file_path.read_text(encoding="utf-8", errors="ignore")


def _read_head(file_path: Path, lines: int = 50) -> str:
    """ファイルの先頭N行を読む（エンコーディング自動検出）"""
    for enc in ("utf-8", "cp932", "euc-jp"):
        try:
            with open(file_path, encoding=enc) as f:
                return "".join(f.readline() for _ in range(lines))
        except (UnicodeDecodeError, UnicodeError):
            continue
        except Exception:
            return ""
    try:
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            return "".join(f.readline() for _ in range(lines))
    except Exception:
        return ""


def _expand_feature_terms(feature: str) -> List[str]:
    """機能名から検索ワードリストを生成する"""
    terms = [feature]
    # 日本語を含む場合は英字部分（括弧内など）も抽出
    english_match = re.findall(r'[A-Za-z][A-Za-z0-9]+', feature)
    terms.extend(english_match)
    # camelCase を小文字に変換
    for e in english_match:
        terms.append(e.lower())
        # camelCase → snake分割
        snake = re.sub(r'([A-Z])', r'_\1', e).lower().strip('_')
        terms.extend(snake.split('_'))
    return list(dict.fromkeys([t for t in terms if len(t) >= 3]))


def _deduplicate_params(params: List[Dict]) -> List[Dict]:
    """同名パラメーターを信頼度優先でマージ"""
    seen = {}
    priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3}
    for p in params:
        name = p["name"]
        if name not in seen or priority.get(p["confidence"], 3) < priority.get(seen[name]["confidence"], 3):
            seen[name] = p
    return list(seen.values())


def _merge_params(params: List[Dict]) -> List[Dict]:
    """全ソースからのパラメーターを信頼度優先でマージ"""
    return _deduplicate_params(params)


def _merge_unique_strings(*groups: List[str]) -> List[str]:
    seen = set()
    ordered = []
    for group in groups:
        for value in group:
            if value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return ordered


def _merge_llm_context_files(*groups: List[Dict]) -> List[Dict]:
    seen = set()
    ordered = []
    for group in groups:
        for item in group:
            key = (item.get("type"), item.get("path"))
            if key in seen:
                continue
            seen.add(key)
            ordered.append(item)
    return ordered


def _merge_discovered_files(base: Dict[str, List[str]], extra: Dict[str, List[str]]) -> Dict[str, List[str]]:
    keys = set(base.keys()) | set(extra.keys())
    return {
        key: _merge_unique_strings(base.get(key, []), extra.get(key, []))
        for key in keys
    }


def _merge_analysis_results(graph_result: Dict, heuristic_result: Dict) -> Dict:
    merged = dict(graph_result)
    merged["project_root"] = heuristic_result.get("project_root", graph_result.get("project_root"))
    merged["feature"] = graph_result.get("feature") or heuristic_result.get("feature")
    merged["discovered_files"] = _merge_discovered_files(
        graph_result.get("discovered_files", {}),
        heuristic_result.get("discovered_files", {}),
    )
    merged["api_calls"] = heuristic_result.get("api_calls", [])
    merged["request_params"] = _merge_params(
        graph_result.get("request_params", []) + heuristic_result.get("request_params", [])
    )
    merged["response_params"] = _merge_params(
        graph_result.get("response_params", []) + heuristic_result.get("response_params", [])
    )
    merged["uncertain"] = _merge_unique_strings(
        graph_result.get("uncertain", []),
        heuristic_result.get("uncertain", []),
    )
    merged["llm_context_files"] = _merge_llm_context_files(
        graph_result.get("llm_context_files", []),
        heuristic_result.get("llm_context_files", []),
    )
    merged["front_sources"] = _merge_unique_strings(graph_result.get("front_sources", []))
    merged["back_sources"] = _merge_unique_strings(graph_result.get("back_sources", []))
    merged["graph_evidence"] = graph_result.get("graph_evidence", [])
    merged["excluded_candidates"] = graph_result.get("excluded_candidates", [])
    if "endpoints" in heuristic_result:
        merged["endpoints"] = heuristic_result["endpoints"]
    return merged


def _augment_backend_from_api_calls(search_root: Path, api_calls: List[Dict]) -> Dict:
    discovered_files = {
        "controllers": [],
        "services": [],
        "mappers": [],
        "mybatis_xml": [],
    }
    endpoints: List[Dict] = []
    request_params: List[Dict] = []
    response_params: List[Dict] = []

    for api_url in _merge_unique_strings(
        [call.get("url", "") for call in api_calls if call.get("url", "").startswith("/api/")]
    ):
        controllers = find_controller_by_url(search_root, api_url)
        for controller in controllers:
            discovered_files["controllers"].append(str(controller))
            controller_endpoints = extract_controller_endpoints(controller, search_root)
            endpoints.extend(controller_endpoints)
            for ep in controller_endpoints:
                for key in ep.get("request_keys_backend", []):
                    request_params.append({
                        "name": key,
                        "source": f"backend_endpoint_request:{controller.name}",
                        "confidence": "HIGH",
                        "context_hint": f"Backend request key for {ep['method']} {ep['path']}",
                    })
                for key in ep.get("response_keys_backend", []):
                    response_params.append({
                        "name": key,
                        "source": f"backend_endpoint_response:{controller.name}",
                        "confidence": "HIGH",
                        "context_hint": f"Backend response key for {ep['method']} {ep['path']}",
                    })

            services = find_service_from_controller(search_root, controller)
            for service in services:
                discovered_files["services"].append(str(service))
                extracted = extract_map_keys_from_java(service)
                request_params.extend(extracted["request"])
                response_params.extend(extracted["response"])

                mappers, xmls = find_mapper_from_service(search_root, service)
                for mapper in mappers:
                    discovered_files["mappers"].append(str(mapper))
                    extracted_mapper = extract_map_keys_from_java(mapper)
                    request_params.extend(extracted_mapper["request"])
                    response_params.extend(extracted_mapper["response"])
                for xml in xmls:
                    discovered_files["mybatis_xml"].append(str(xml))
                    extracted_xml = extract_mybatis_params(xml)
                    request_params.extend(extracted_xml["input"])
                    response_params.extend(extracted_xml["result"])

    return {
        "api_calls": _merge_backend_endpoint_details(api_calls, endpoints),
        "endpoints": endpoints,
        "discovered_files": {
            key: _merge_unique_strings(values)
            for key, values in discovered_files.items()
        },
        "request_params": _merge_params(request_params),
        "response_params": _merge_params(response_params),
    }


def _build_llm_context_priority(
    xmls: List[Path], mappers: List[Path], services: List[Path],
    controllers: List[Path], api_files: List[Path], vue_files: List[Path]
) -> List[Dict]:
    """LLMに渡すファイルの優先リストを構築する"""
    priority_list = []

    for f in xmls:
        priority_list.append({"priority": 1, "type": "mybatis_xml", "path": str(f)})
    for f in mappers:
        priority_list.append({"priority": 1, "type": "java_mapper", "path": str(f)})
    for f in services:
        priority_list.append({"priority": 2, "type": "java_service", "path": str(f)})
    for f in api_files:
        priority_list.append({"priority": 2, "type": "vue_api_service", "path": str(f)})
    for f in controllers:
        priority_list.append({"priority": 3, "type": "java_controller", "path": str(f)})
    for f in vue_files:
        priority_list.append({"priority": 3, "type": "vue_component", "path": str(f)})

    return sorted(priority_list, key=lambda x: x["priority"])


def _resolve_project_root_arg(project_arg: Optional[str], front_root: Optional[str], back_root: Optional[str]) -> str:
    if project_arg:
        return str(Path(project_arg).resolve())
    if back_root:
        back_root_path = Path(back_root).resolve()
        if back_root_path.name == "java" and back_root_path.parent.name == "main" and back_root_path.parent.parent.name == "src":
            inferred_root = back_root_path.parent.parent.parent
        else:
            inferred_root = back_root_path
    else:
        inferred_root = None
    if front_root and back_root:
        front_path = Path(front_root).resolve()
        back_path = inferred_root or Path(back_root).resolve()
        common_root = Path(os.path.commonpath([str(front_path), str(back_path)]))
        return str(common_root)
    if front_root:
        return str(Path(front_root).resolve())
    if inferred_root:
        return str(inferred_root)
    return str(Path.cwd().resolve())


def run_analysis(
    *,
    project: Optional[str] = None,
    engine: str = "heuristic",
    feature: Optional[str] = None,
    url: Optional[str] = None,
    files: Optional[List[str]] = None,
    mapper: Optional[str] = None,
    scope_feature: Optional[str] = None,
    scope_url: Optional[str] = None,
    scope_files: Optional[List[str]] = None,
    front_root: Optional[str] = None,
    back_root: Optional[str] = None,
    front_repo_name: str = "socia2026",
    back_repo_name: str = "back",
    gitnexus_limit: int = 8,
) -> Dict:
    project_root = _resolve_project_root_arg(project, front_root, back_root)
    effective_feature = scope_feature or feature
    effective_url = scope_url or url
    merged_scope_files = _merge_unique_strings(scope_files or [], files or [])

    if not effective_feature and not effective_url and not merged_scope_files and not mapper:
        raise ValueError("One of feature, url, files, or mapper must be provided.")

    if engine in ("gitnexus", "hybrid"):
        if not front_root or not back_root:
            raise ValueError("gitnexus and hybrid analysis require both front_root and back_root.")
        if not effective_feature and not effective_url and not merged_scope_files:
            raise ValueError("gitnexus and hybrid analysis require scope_feature, scope_url, or scope_files.")
        return analyze_scope_with_gitnexus(
            project_root_str=project_root,
            front_root=front_root,
            back_root=back_root,
            feature=effective_feature,
            url_path=effective_url,
            scope_files=merged_scope_files,
            mapper_path=mapper,
            engine=engine,
            front_repo_name=front_repo_name,
            back_repo_name=back_repo_name,
            gitnexus_limit=gitnexus_limit,
        )

    if merged_scope_files or mapper:
        return analyze_direct_files(project_root, merged_scope_files, mapper)
    if effective_feature:
        return analyze_feature(project_root, effective_feature)
    return analyze_by_url(project_root, effective_url or "")


# ============================================================
# エントリーポイント
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Spring Boot + Vue プロジェクトのコード分析ツール（UI設計書生成用）"
    )
    parser.add_argument("--project", help="プロジェクトルートディレクトリ")
    parser.add_argument("--engine", choices=["heuristic", "gitnexus", "hybrid"], default="heuristic",
                        help="解析エンジン。hybrid は gitnexus で scope を絞ってから既存解析で補完する")
    parser.add_argument("--feature", help="機能名（例: 現職エリア, gensyokuArea）")
    parser.add_argument("--url", help="エンドポイントURL（例: /api/gensyokuarea/showBlock）")
    parser.add_argument("--files", nargs="+", help="直接解析するファイルパスのリスト")
    parser.add_argument("--mapper", help="直接解析するMyBatis XMLファイルパス")
    parser.add_argument("--scope-feature", help="scope 用の機能名。--feature より優先")
    parser.add_argument("--scope-url", help="scope 用のURL。--url より優先")
    parser.add_argument("--scope-files", nargs="+", help="scope 用のファイルパス。--files と併用可")
    parser.add_argument("--front-root", help="front 側の固定ルートパス")
    parser.add_argument("--back-root", help="back 側の固定ルートパス")
    parser.add_argument("--front-repo-name", default="socia2026", help="gitnexus 上の front 検索用 repo 名")
    parser.add_argument("--back-repo-name", default="back", help="gitnexus 上の back 検索用 repo 名")
    parser.add_argument("--gitnexus-limit", type=int, default=8, help="gitnexus query の件数上限")
    parser.add_argument("--output", default="analysis_result.json", help="出力JSONファイル名")
    parser.add_argument("--print-context", action="store_true",
                        help="LLMに渡すべきファイルの内容を標準出力に出力（コンテキスト構築用）")

    args = parser.parse_args()
    try:
        result = run_analysis(
            project=args.project,
            engine=args.engine,
            feature=args.feature,
            url=args.url,
            files=args.files,
            mapper=args.mapper,
            scope_feature=args.scope_feature,
            scope_url=args.scope_url,
            scope_files=args.scope_files,
            front_root=args.front_root,
            back_root=args.back_root,
            front_repo_name=args.front_repo_name,
            back_repo_name=args.back_repo_name,
            gitnexus_limit=args.gitnexus_limit,
        )
    except ValueError as exc:
        print(f"エラー: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        print(f"エラー: {exc}")
        sys.exit(1)

    # JSON出力
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\n[完了] 結果を {args.output} に保存しました")
    print(f"  リクエストパラメーター候補: {len(result['request_params'])}件")
    print(f"  レスポンスパラメーター候補: {len(result['response_params'])}件")
    print(f"  未確認事項: {len(result['uncertain'])}件")

    if result["uncertain"]:
        print("\n[要確認]")
        for u in result["uncertain"]:
            print(f"  [warn] {u}")

    # LLMコンテキスト出力モード
    if args.print_context:
        print("\n" + "=" * 60)
        print("LLMコンテキスト用ファイル内容（優先度順）")
        print("=" * 60)
        for item in result["llm_context_files"]:
            p = Path(item["path"])
            print(f"\n--- [{item['type']}] {p.name} ---")
            try:
                print(_read_file_text(p)[:3000])
                if p.stat().st_size > 3000:
                    print(f"... (以下省略。全{p.stat().st_size}バイト)")
            except Exception as e:
                print(f"読み込みエラー: {e}")


if __name__ == "__main__":
    main()
