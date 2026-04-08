#!/usr/bin/env python3
"""
extract_api_inventory.py
------------------------
Spring Boot Controller から API 在庫表を抽出する。

出力列:
  - 分类: `--category-mode` に応じて `jp.co.fminc.socia...` から抽出
  - 功能描述: API メソッド直前 Javadoc の先頭1行
  - API路径: class-level + method-level mapping を連結したパス
  - HTTP方法
  - 废弃

判定ルール:
  - 否: front/src 配下に呼び出し証拠あり
  - 是(疑似): front/src 配下に呼び出し証拠なし
  - 判定不可: API パスが定数式などで文字列に解決できない

Usage:
  python extract_api_inventory.py \
    --back-root D:\\work\\backend\\src\\main\\java \
    --front-root D:\\work\\frontend\\src \
    --output-prefix D:\\tmp\\api_inventory_generated
"""

from __future__ import annotations

import argparse
import csv
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


PACKAGE_PATTERN = re.compile(r"^\s*package\s+([a-zA-Z0-9_.]+)\s*;")
FULL_STRING_LITERAL_PATTERN = re.compile(r'^(?:"([^"]*)"|\'([^\']*)\')$')
CONST_STRING_PATTERN = re.compile(
    r'(?:const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*(?::[^=]+)?=\s*["\']([^"\']+)["\']'
)
DIRECT_API_LITERAL_PATTERN = re.compile(r'["\'](/api/[^"\']*)["\']')
VAR_CONCAT_PATTERN = re.compile(r'([A-Za-z_$][A-Za-z0-9_$]*)\s*\+\s*["\']([^"\']*)["\']')
REQUEST_METHOD_PATTERN = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH)")
VALUE_ARG_PATTERN = re.compile(r"\b(?:value|path)\s*=\s*([^,)]*)")
MAPPING_ANNOTATION_PATTERN = re.compile(
    r"@(?P<name>RequestMapping|GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping)\b"
)
METHOD_NAME_PATTERN = re.compile(r"\b([A-Za-z_$][A-Za-z0-9_$]*)\s*\(")

SOURCE_EXTENSIONS = {".ts", ".js", ".vue", ".tsx", ".jsx", ".mjs", ".mts", ".cts"}


@dataclass
class EndpointRecord:
    category: str
    description: str
    api_path: str
    http_method: str
    unused_status: str
    source_file: str
    method_name: str
    controller_class: str
    confidence: str
    dedupe_key: str
    evidence: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_first_comment_line(raw_comment: str) -> str:
    for line in raw_comment.splitlines():
        stripped = line.strip()
        stripped = stripped.removeprefix("/**").removeprefix("/*").removesuffix("*/").strip()
        if stripped.startswith("*"):
            stripped = stripped[1:].strip()
        if not stripped or stripped.startswith("@"):
            continue
        return stripped
    return ""


def derive_category(package_name: str, relative_path: Path, category_mode: str) -> str:
    marker = "jp.co.fminc.socia."
    if package_name.startswith(marker):
        remainder = package_name[len(marker):]
        parts = [part for part in remainder.split(".") if part]
        if parts:
            if category_mode == "module":
                if "controller" in parts:
                    controller_index = parts.index("controller")
                    if controller_index > 0:
                        return parts[controller_index - 1]
                if len(parts) >= 2:
                    return parts[-2]
                return parts[-1]
            return parts[0]
    if relative_path.parts:
        if category_mode == "module" and len(relative_path.parts) >= 2:
            return relative_path.parts[-2]
        return relative_path.parts[0]
    return "unknown"


def clean_mapping_expr(expr: str) -> str:
    return expr.strip()


def extract_mapping_path(annotation_text: str) -> str:
    named = VALUE_ARG_PATTERN.search(annotation_text)
    if named:
        return clean_mapping_expr(named.group(1))
    body_start = annotation_text.find("(")
    body_end = annotation_text.rfind(")")
    if body_start == -1 or body_end == -1 or body_end <= body_start:
        return ""
    body = annotation_text[body_start + 1:body_end].strip()
    if not body:
        return ""
    first_arg = body.split(",", 1)[0].strip()
    if first_arg.startswith("method ="):
        return ""
    return clean_mapping_expr(first_arg)


def literalize_mapping(expr: str) -> Tuple[List[str], bool]:
    expr = expr.strip()
    if not expr:
        return [""], True
    if expr.startswith("{") and expr.endswith("}"):
        values = []
        for item in [part.strip() for part in expr[1:-1].split(",") if part.strip()]:
            literal_match = FULL_STRING_LITERAL_PATTERN.match(item)
            if not literal_match:
                return [expr], False
            values.append(literal_match.group(1) or literal_match.group(2) or "")
        return values or [""], True
    literal_match = FULL_STRING_LITERAL_PATTERN.match(expr)
    if literal_match:
        return [literal_match.group(1) or literal_match.group(2) or ""], True
    return [expr], False


def normalize_joined_path(base_path: str, method_path: str) -> str:
    if not base_path and not method_path:
        return ""
    if not method_path:
        return base_path
    if not base_path:
        return method_path
    if not base_path.endswith("/"):
        base_path += "/"
    return base_path.rstrip("/") + "/" + method_path.lstrip("/")


def normalize_api_path(path: str) -> str:
    normalized = re.sub(r"/+", "/", (path or "").strip())
    if normalized and not normalized.startswith("/"):
        normalized = f"/{normalized}"
    return normalized.rstrip("/") or "/"


def extract_http_method(annotation_text: str, annotation_name: str) -> str:
    explicit = REQUEST_METHOD_PATTERN.search(annotation_text)
    if explicit:
        return explicit.group(1)
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
    return "POST"


def extract_method_name(signature_text: str) -> str:
    before_paren = signature_text.split("{", 1)[0]
    matches = METHOD_NAME_PATTERN.findall(before_paren)
    if not matches:
        return "unknown"
    ignored = {"if", "for", "while", "switch", "catch", "return", "new"}
    for candidate in reversed(matches):
        if candidate not in ignored:
            return candidate
    return matches[-1]


def collect_annotation_block(lines: Sequence[str], start_index: int) -> Tuple[List[str], int]:
    annotations: List[str] = []
    i = start_index
    while i < len(lines):
        if not lines[i].lstrip().startswith("@"):
            break
        current = [lines[i].rstrip("\n")]
        paren_depth = lines[i].count("(") - lines[i].count(")")
        i += 1
        while i < len(lines) and paren_depth > 0:
            current.append(lines[i].rstrip("\n"))
            paren_depth += lines[i].count("(") - lines[i].count(")")
            i += 1
        annotations.append(" ".join(part.strip() for part in current))
    return annotations, i


def collect_signature(lines: Sequence[str], start_index: int) -> Tuple[str, int]:
    parts: List[str] = []
    i = start_index
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        parts.append(stripped)
        if "{" in stripped or stripped.endswith(";"):
            i += 1
            break
        i += 1
    return " ".join(parts), i


def extract_class_base_mapping(annotations: Sequence[str]) -> Tuple[str, bool, str]:
    for annotation in annotations:
        match = MAPPING_ANNOTATION_PATTERN.search(annotation)
        if not match:
            continue
        mapping_expr = extract_mapping_path(annotation)
        mapping_values, is_literal = literalize_mapping(mapping_expr)
        return mapping_values[0], is_literal, annotation
    return "", True, ""


def derive_confidence(is_literal: bool, summary_inferred: bool) -> str:
    if not is_literal:
        return "LOW"
    if summary_inferred:
        return "MEDIUM"
    return "HIGH"


def parse_controller_file(controller_path: Path, java_root: Path, category_mode: str) -> List[EndpointRecord]:
    content = read_text(controller_path)
    if "@RestController" not in content and "@Controller" not in content:
        return []

    relative_path = controller_path.relative_to(java_root)
    package_name = ""
    for line in content.splitlines():
        match = PACKAGE_PATTERN.match(line)
        if match:
            package_name = match.group(1)
            break

    category = derive_category(package_name, relative_path, category_mode)
    lines = content.splitlines(keepends=True)

    records: List[EndpointRecord] = []
    pending_comment = ""
    class_base_path = ""
    class_base_literal = True
    class_mapping_annotation = ""
    controller_class = controller_path.stem
    i = 0

    while i < len(lines):
        stripped = lines[i].strip()

        if stripped.startswith("/**"):
            block_lines = [lines[i]]
            i += 1
            while i < len(lines):
                block_lines.append(lines[i])
                if "*/" in lines[i]:
                    i += 1
                    break
                i += 1
            pending_comment = extract_first_comment_line("".join(block_lines))
            continue

        if stripped.startswith("@"):
            annotations, next_index = collect_annotation_block(lines, i)
            signature_text, after_signature = collect_signature(lines, next_index)

            if " class " in f" {signature_text} ":
                class_base_path, class_base_literal, class_mapping_annotation = extract_class_base_mapping(annotations)
                pending_comment = ""
                i = after_signature
                continue

            mapping_annotation = None
            mapping_name = ""
            for annotation in annotations:
                match = MAPPING_ANNOTATION_PATTERN.search(annotation)
                if match:
                    mapping_annotation = annotation
                    mapping_name = match.group("name")
                    break

            if mapping_annotation and signature_text:
                method_expr = extract_mapping_path(mapping_annotation)
                method_paths, method_is_literal = literalize_mapping(method_expr)
                http_method = extract_http_method(mapping_annotation, mapping_name)
                method_name = extract_method_name(signature_text)
                summary = pending_comment or method_name
                summary_inferred = summary == method_name
                controller_warnings = []

                if not (class_base_literal and method_is_literal):
                    controller_warnings.append("non_literal_mapping")
                if summary_inferred:
                    controller_warnings.append("summary_inferred_from_method_name")

                for method_path in method_paths:
                    api_path = normalize_api_path(normalize_joined_path(class_base_path, method_path))
                    confidence = derive_confidence(class_base_literal and method_is_literal, summary_inferred)
                    records.append(
                        EndpointRecord(
                            category=category,
                            description=summary,
                            api_path=api_path,
                            http_method=http_method,
                            unused_status="判定不可"
                            if not (class_base_literal and method_is_literal) or not api_path.startswith("/api/")
                            else "",
                            source_file=str(controller_path),
                            method_name=method_name,
                            controller_class=controller_class,
                            confidence=confidence,
                            dedupe_key=f"{http_method} {api_path}",
                            evidence={
                                "controller_file": str(controller_path),
                                "controller_class": controller_class,
                                "controller_method": method_name,
                                "package": package_name,
                                "class_mapping": class_mapping_annotation,
                                "method_mapping": mapping_annotation,
                            },
                            warnings=list(controller_warnings),
                        )
                    )
                pending_comment = ""

            i = after_signature
            continue

        if stripped:
            pending_comment = ""
        i += 1

    return records


def collect_front_usage(front_root: Optional[Path]) -> Tuple[set[str], set[str]]:
    if front_root is None or not front_root.exists():
        return set(), set()

    exact_urls: set[str] = set()
    prefix_urls: set[str] = set()

    for path in front_root.rglob("*"):
        if path.suffix.lower() not in SOURCE_EXTENSIONS or not path.is_file():
            continue

        text = read_text(path)
        const_map: Dict[str, str] = {name: value for name, value in CONST_STRING_PATTERN.findall(text)}

        for match in DIRECT_API_LITERAL_PATTERN.findall(text):
            exact_urls.add(match.strip())

        for var_name, suffix in VAR_CONCAT_PATTERN.findall(text):
            base = const_map.get(var_name)
            if not base or not base.startswith("/api/"):
                continue
            combined = normalize_api_path(base + suffix)
            exact_urls.add(combined)
            if combined.endswith("/{id}"):
                prefix_urls.add(combined[:-4])

    return exact_urls, prefix_urls


def endpoint_usage_status(api_path: str, exact_urls: set[str], prefix_urls: set[str], current_status: str) -> str:
    if current_status:
        return current_status

    normalized = normalize_api_path(api_path)
    if not normalized:
        return "判定不可"

    placeholder_match = re.search(r"\{[^}]+\}", normalized)
    if placeholder_match:
        prefix = normalized[:placeholder_match.start()]
        if any(url.startswith(prefix) for url in exact_urls | prefix_urls):
            return "否"
        return "是(疑似)"

    if normalized in exact_urls:
        return "否"
    if any(prefix == normalized or prefix.startswith(normalized) for prefix in prefix_urls):
        return "否"
    return "是(疑似)"


def build_inventory(
    back_root: Path,
    front_root: Optional[Path],
    controller_keywords: Optional[Sequence[str]] = None,
    category_mode: str = "top",
) -> List[EndpointRecord]:
    exact_urls, prefix_urls = collect_front_usage(front_root)
    records: List[EndpointRecord] = []
    normalized_keywords = [keyword.lower() for keyword in (controller_keywords or []) if keyword.strip()]

    for controller_path in sorted(back_root.rglob("*Controller.java")):
        controller_path_lower = str(controller_path).lower()
        if normalized_keywords and not any(keyword in controller_path_lower for keyword in normalized_keywords):
            continue
        file_records = parse_controller_file(controller_path, back_root, category_mode)
        for record in file_records:
            record.unused_status = endpoint_usage_status(
                record.api_path,
                exact_urls,
                prefix_urls,
                record.unused_status,
            )
            records.append(record)

    records.sort(key=lambda r: (r.category, r.api_path, r.http_method, r.description))
    return records


def write_csv(records: Sequence[EndpointRecord], output_path: Path) -> None:
    with output_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["分类", "功能描述", "API路径", "HTTP方法", "废弃"])
        for record in records:
            writer.writerow(
                [
                    record.category,
                    record.description,
                    record.api_path,
                    record.http_method,
                    record.unused_status,
                ]
            )


def write_markdown(records: Sequence[EndpointRecord], output_path: Path) -> None:
    lines = [
        "# API Inventory",
        "",
        "判定规则：",
        "- `否`：在前端源码中检索到调用证据。",
        "- `是(疑似)`：当前前端源码中未检索到调用证据。",
        "- `判定不可`：API 路径不是可解析的字面量（例如常量表达式）。",
        "",
        "| 分类 | 功能描述 | API路径 | HTTP方法 | 废弃 |",
        "|---|---|---|---|---|",
    ]
    for record in records:
        lines.append(
            f"| {record.category} | {record.description} | `{record.api_path}` | {record.http_method} | {record.unused_status} |"
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def infer_back_root_from_path(target_path: Path) -> Optional[Path]:
    cursor = target_path if target_path.is_dir() else target_path.parent
    for candidate in [cursor] + list(cursor.parents):
        if candidate.name != "java":
            continue
        if candidate.parent.name == "main" and candidate.parent.parent.name == "src":
            return candidate
    return None


def resolve_back_root(back_root: Optional[str], target_mode: str, resolved_target: str) -> Path:
    if back_root:
        back_root_path = Path(back_root).expanduser().resolve()
    elif target_mode == "path":
        inferred = infer_back_root_from_path(Path(resolved_target))
        if inferred is None:
            raise ValueError("Unable to infer --back-root from --path. Please provide --back-root explicitly.")
        back_root_path = inferred
    else:
        raise ValueError("--back-root is required when using --api or --package.")

    if not back_root_path.exists():
        raise ValueError(f"Backend root not found: {back_root_path}")
    return back_root_path


def resolve_front_root(front_root: Optional[str]) -> Optional[Path]:
    if not front_root:
        return None
    front_root_path = Path(front_root).expanduser().resolve()
    if not front_root_path.exists():
        raise ValueError(f"Frontend root not found: {front_root_path}")
    return front_root_path


def resolve_controller_paths(back_root: Path, target_mode: str, resolved_target: str) -> List[Path]:
    if target_mode == "api":
        return sorted(back_root.rglob("*Controller.java"))

    if target_mode == "package":
        package_root = (back_root / Path(resolved_target)).resolve()
        if not package_root.exists():
            raise ValueError(f"Package path not found under backend root: {package_root}")
        return sorted(package_root.rglob("*Controller.java"))

    target_path = Path(resolved_target).expanduser().resolve()
    if not target_path.exists():
        raise ValueError(f"Target path not found: {target_path}")
    if target_path.is_file():
        return [target_path]
    return sorted(target_path.rglob("*Controller.java"))


def endpoint_record_to_candidate(record: EndpointRecord) -> Dict[str, Any]:
    return {
        "id": record.dedupe_key,
        "method": record.http_method,
        "path": record.api_path,
        "summary": record.description,
        "controller_class": record.controller_class,
        "controller_method": record.method_name,
        "confidence": record.confidence,
        "dedupe_key": record.dedupe_key,
        "evidence": {
            **record.evidence,
            "usage_status": record.unused_status,
        },
        "warnings": list(record.warnings),
    }


def build_scan_candidates(
    *,
    back_root: Path,
    front_root: Optional[Path],
    controller_paths: Sequence[Path],
    target_api: Optional[str] = None,
    category_mode: str = "top",
) -> List[Dict[str, Any]]:
    exact_urls, prefix_urls = collect_front_usage(front_root)
    candidates: List[Dict[str, Any]] = []
    normalized_target_api = normalize_api_path(target_api) if target_api else None

    for controller_path in sorted(controller_paths):
        file_records = parse_controller_file(controller_path, back_root, category_mode)
        for record in file_records:
            record.unused_status = endpoint_usage_status(
                record.api_path,
                exact_urls,
                prefix_urls,
                record.unused_status,
            )
            candidate = endpoint_record_to_candidate(record)
            if normalized_target_api and normalize_api_path(candidate["path"]) != normalized_target_api:
                continue
            candidates.append(candidate)

    candidates.sort(
        key=lambda candidate: (
            candidate["path"],
            candidate["method"],
            candidate["controller_class"],
            candidate["controller_method"],
        )
    )
    return candidates


def dedupe_scan_candidates(candidates: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    deduped: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    seen: Dict[str, Dict[str, Any]] = {}

    for candidate in candidates:
        dedupe_key = candidate["dedupe_key"]
        if dedupe_key not in seen:
            seen[dedupe_key] = candidate
            deduped.append(candidate)
            continue

        kept = seen[dedupe_key]
        warnings.append(
            {
                "code": "duplicate_endpoint",
                "dedupe_key": dedupe_key,
                "message": (
                    f"Duplicate endpoint {dedupe_key} detected in "
                    f"{candidate['controller_class']}.{candidate['controller_method']}; "
                    f"keeping {kept['controller_class']}.{kept['controller_method']}."
                ),
                "kept": kept["id"],
                "discarded": candidate["id"],
                "evidence": [kept["evidence"], candidate["evidence"]],
            }
        )

    for candidate in deduped:
        for warning_code in candidate.get("warnings", []):
            warnings.append(
                {
                    "code": warning_code,
                    "candidate_id": candidate["id"],
                    "message": f"{candidate['dedupe_key']}: {warning_code}",
                    "evidence": candidate["evidence"],
                }
            )

    return deduped, warnings


def build_scan_artifact(
    *,
    target_mode: str,
    target_value: str,
    resolved_target: str,
    back_root: Optional[str],
    front_root: Optional[str],
    output_json: str,
    verbose: bool = False,
) -> Dict[str, Any]:
    back_root_path = resolve_back_root(back_root, target_mode, resolved_target)
    front_root_path = resolve_front_root(front_root)
    controller_paths = resolve_controller_paths(back_root_path, target_mode, resolved_target)
    candidates = build_scan_candidates(
        back_root=back_root_path,
        front_root=front_root_path,
        controller_paths=controller_paths,
        target_api=target_value if target_mode == "api" else None,
    )
    if not candidates:
        raise ValueError(
            "No API candidates found for the supplied target. Check the target scope or provide a different --path/--package/--api."
        )

    deduped_candidates, warnings = dedupe_scan_candidates(candidates)
    scan_payload = {
        "tool": {
            "name": "docapi",
            "command": "scan",
            "version": "0.1.4",
        },
        "inputs": {
            "mode": target_mode,
            "target": target_value,
            "resolved_target": resolved_target,
            "verbose": verbose,
        },
        "roots": {
            "back_root": str(back_root_path),
            "front_root": str(front_root_path) if front_root_path else None,
        },
        "dedupe": {
            "key": "HTTP method + API path",
            "total_candidates": len(candidates),
            "kept_candidates": len(deduped_candidates),
            "duplicate_warnings": len([warning for warning in warnings if warning["code"] == "duplicate_endpoint"]),
        },
        "candidates": deduped_candidates,
        "warnings": warnings,
    }
    return {
        "scan": scan_payload,
        "candidates": deduped_candidates,
        "warnings": warnings,
        "output_json": str(Path(output_json).expanduser().resolve()),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract API inventory from Spring Boot controllers.")
    parser.add_argument("--back-root", required=True, help="Backend Java source root (e.g. back/src/main/java)")
    parser.add_argument("--front-root", help="Frontend source root (e.g. front/src)")
    parser.add_argument("--output-prefix", required=True, help="Output prefix without extension")
    parser.add_argument(
        "--controller-keyword",
        action="append",
        default=[],
        help="Only include controllers whose path contains this keyword. Repeatable.",
    )
    parser.add_argument(
        "--category-mode",
        choices=["top", "module"],
        default="top",
        help="Category extraction mode: top=first package after socia, module=controller parent package.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    back_root = Path(args.back_root).resolve()
    front_root = Path(args.front_root).resolve() if args.front_root else None
    output_prefix = Path(args.output_prefix).resolve()

    if not back_root.exists():
        raise SystemExit(f"Backend root not found: {back_root}")
    if front_root and not front_root.exists():
        raise SystemExit(f"Frontend root not found: {front_root}")

    records = build_inventory(back_root, front_root, args.controller_keyword, args.category_mode)

    csv_path = output_prefix.with_suffix(".csv")
    md_path = output_prefix.with_suffix(".md")

    write_csv(records, csv_path)
    write_markdown(records, md_path)

    print(f"Generated {len(records)} rows")
    print(f"CSV: {csv_path}")
    print(f"MD : {md_path}")


if __name__ == "__main__":
    main()
