#!/usr/bin/env python3
"""
gitnexus_adapter.py - Socia2026 向けの gitnexus 範囲限定アダプター

目的:
- front/back を固定した状態で gitnexus の定義検索を実行する
- 機能名 / API URL / ファイルパスで scope を絞り込む
- analyze_code.py が消費しやすい JSON へ正規化する
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


IGNORED_URL_SEGMENTS = {"api", "v1", "v2"}


def collect_scope_context(
    *,
    front_root: str,
    back_root: str,
    feature: Optional[str] = None,
    url: Optional[str] = None,
    scope_files: Optional[Sequence[str]] = None,
    front_repo_name: str = "socia2026",
    back_repo_name: str = "back",
    limit: int = 8,
) -> Dict:
    """gitnexus から範囲限定の証拠を集める。"""
    front_root_path = Path(front_root).resolve()
    back_root_path = Path(back_root).resolve()

    explicit_scope_files = _resolve_scope_files(scope_files or [], front_root_path, back_root_path)
    query_terms = _build_query_terms(feature, url, explicit_scope_files)
    scope = {
        "feature": feature or "",
        "url": url or "",
        "files": [str(p) for p in explicit_scope_files],
        "front_root": str(front_root_path),
        "back_root": str(back_root_path),
        "mode": "code-first",
    }

    result = {
        "feature": feature or url or "scope_analysis",
        "project_root": str(front_root_path.parents[1]) if len(front_root_path.parents) >= 2 else str(front_root_path.parent),
        "scope": scope,
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
        "front_sources": [],
        "back_sources": [],
        "graph_evidence": [],
        "excluded_candidates": [],
    }

    if not query_terms and not explicit_scope_files:
        result["uncertain"].append("scope が不足しています。機能名 / API URL / ファイルパスのいずれかを指定してください。")
        return result

    repo_specs = [
        {
            "repo_name": front_repo_name,
            "root": front_root_path,
            "label": "front",
        },
        {
            "repo_name": back_repo_name,
            "root": back_root_path,
            "label": "back",
        },
    ]

    seen_paths = set()
    seen_evidence = set()
    explicit_set = {str(p) for p in explicit_scope_files}

    for file_path in explicit_scope_files:
        if _is_within_root(file_path, front_root_path):
            result["front_sources"].append(str(file_path))
        elif _is_within_root(file_path, back_root_path):
            result["back_sources"].append(str(file_path))
        _add_discovered_file(result["discovered_files"], file_path)
        result["llm_context_files"].append(
            {"priority": 1, "type": "scope_file", "path": str(file_path)}
        )

    for repo in repo_specs:
        repo_name = repo["repo_name"]
        repo_root = repo["root"]
        repo_label = repo["label"]

        if not repo_root.exists():
            result["uncertain"].append(f"{repo_label} root が存在しません: {repo_root}")
            continue

        for term in query_terms:
            payload = _run_gitnexus_json(["query", "-r", repo_name, term, "-l", str(limit)])
            if payload is None:
                result["uncertain"].append(f"gitnexus query 失敗: repo={repo_name}, term={term}")
                continue

            for definition in payload.get("definitions", []):
                accepted, absolute_path, reason = _accept_definition(
                    definition=definition,
                    repo_root=repo_root,
                    feature=feature,
                    url=url,
                    explicit_scope_files=explicit_scope_files,
                )
                file_path = definition.get("filePath", "")
                if not accepted:
                    result["excluded_candidates"].append(
                        {
                            "repo": repo_name,
                            "query": term,
                            "file_path": file_path,
                            "reason": reason,
                        }
                    )
                    continue

                evidence_key = (
                    repo_name,
                    term,
                    definition.get("id", ""),
                    str(absolute_path),
                )
                if evidence_key in seen_evidence:
                    continue
                seen_evidence.add(evidence_key)

                if str(absolute_path) not in seen_paths:
                    seen_paths.add(str(absolute_path))
                    if repo_label == "front":
                        result["front_sources"].append(str(absolute_path))
                    else:
                        result["back_sources"].append(str(absolute_path))
                    _add_discovered_file(result["discovered_files"], absolute_path)
                    result["llm_context_files"].append(
                        {
                            "priority": 2,
                            "type": f"gitnexus_{repo_label}",
                            "path": str(absolute_path),
                        }
                    )

                result["graph_evidence"].append(
                    {
                        "repo": repo_name,
                        "scope_term": term,
                        "kind": _definition_kind(definition),
                        "name": definition.get("name", ""),
                        "file_path": file_path,
                        "absolute_path": str(absolute_path),
                        "start_line": definition.get("startLine"),
                        "end_line": definition.get("endLine"),
                    }
                )

            context_payload = _run_gitnexus_json(["context", "-r", repo_name, term])
            if not context_payload or context_payload.get("status") != "found":
                continue

            symbol = context_payload.get("symbol") or {}
            symbol_path = symbol.get("filePath")
            if not symbol_path:
                continue
            absolute_symbol_path = _resolve_gitnexus_path(symbol_path, repo_root)
            if not _is_within_root(absolute_symbol_path, repo_root):
                continue

            if explicit_set and str(absolute_symbol_path) not in explicit_set:
                if not _matches_scope(absolute_symbol_path, symbol.get("name", ""), feature, url, explicit_scope_files):
                    continue
            elif not _matches_scope(absolute_symbol_path, symbol.get("name", ""), feature, url, explicit_scope_files):
                continue

            result["graph_evidence"].append(
                {
                    "repo": repo_name,
                    "scope_term": term,
                    "kind": "context",
                    "name": symbol.get("name", ""),
                    "file_path": symbol_path,
                    "absolute_path": str(absolute_symbol_path),
                    "start_line": None,
                    "end_line": None,
                }
            )

    if not result["front_sources"] and not result["back_sources"]:
        result["uncertain"].append("gitnexus から scope 内の候補ファイルを特定できませんでした。scope を狭めるか、より具体的なファイルパスを指定してください。")
    else:
        if not result["front_sources"]:
            result["uncertain"].append("front 側の候補が見つかりませんでした。front のファイルパスか画面名を追加してください。")
        if not result["back_sources"]:
            result["uncertain"].append("back 側の候補が見つかりませんでした。API URL か back のファイルパスを追加してください。")

    result["front_sources"] = _dedupe_strings(result["front_sources"])
    result["back_sources"] = _dedupe_strings(result["back_sources"])
    result["llm_context_files"] = _dedupe_context_files(result["llm_context_files"])
    return result


def _run_gitnexus_json(args: Sequence[str]) -> Optional[Dict]:
    command = _resolve_gitnexus_command()
    if not command:
        return None

    completed = subprocess.run(
        [*command, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        check=False,
    )
    if completed.returncode != 0:
        return None
    stdout = completed.stdout.strip()
    if not stdout:
        return {}
    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        return None


def _resolve_gitnexus_command() -> Optional[List[str]]:
    for candidate in ("gitnexus.cmd", "gitnexus.exe", "gitnexus.ps1", "gitnexus"):
        path = shutil.which(candidate)
        if not path:
            continue
        if path.lower().endswith(".ps1"):
            return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        return [path]
    return None


def _resolve_scope_files(
    scope_files: Sequence[str],
    front_root: Path,
    back_root: Path,
) -> List[Path]:
    resolved = []
    for raw in scope_files:
        candidate = Path(raw)
        if candidate.is_absolute():
            resolved_path = candidate.resolve()
        else:
            front_candidate = (front_root / candidate).resolve()
            back_candidate = (back_root / candidate).resolve()
            if front_candidate.exists():
                resolved_path = front_candidate
            elif back_candidate.exists():
                resolved_path = back_candidate
            else:
                resolved_path = front_candidate
        if resolved_path.exists():
            resolved.append(resolved_path)
    return _dedupe_paths(resolved)


def _build_query_terms(feature: Optional[str], url: Optional[str], scope_files: Sequence[Path]) -> List[str]:
    terms: List[str] = []
    if feature:
        terms.extend(_expand_feature_terms(feature))
    if url:
        terms.append(url)
        terms.extend([part for part in url.split("/") if part and part not in IGNORED_URL_SEGMENTS])
    for file_path in scope_files:
        terms.append(file_path.stem)
        if file_path.parent.name:
            terms.append(file_path.parent.name)
    filtered = []
    seen = set()
    for term in terms:
        normalized = term.strip()
        if len(normalized) < 3:
            continue
        key = normalized.lower()
        if key in seen:
            continue
        seen.add(key)
        filtered.append(normalized)
        if len(filtered) >= 8:
            break
    return filtered


def _expand_feature_terms(feature: str) -> List[str]:
    terms = [feature]
    english_match = re.findall(r"[A-Za-z][A-Za-z0-9]+", feature)
    for token in english_match:
        terms.append(token)
        terms.append(token.lower())
        snake = re.sub(r"([A-Z])", r"_\1", token).lower().strip("_")
        terms.extend([part for part in snake.split("_") if part])
    return terms


def _accept_definition(
    *,
    definition: Dict,
    repo_root: Path,
    feature: Optional[str],
    url: Optional[str],
    explicit_scope_files: Sequence[Path],
) -> Tuple[bool, Path, str]:
    file_path = definition.get("filePath", "")
    absolute_path = _resolve_gitnexus_path(file_path, repo_root)
    if not _is_within_root(absolute_path, repo_root):
        return False, absolute_path, "outside_configured_root"
    if not absolute_path.exists():
        return False, absolute_path, "file_missing_on_disk"
    symbol_name = definition.get("name", "")
    if not _matches_scope(absolute_path, symbol_name, feature, url, explicit_scope_files):
        return False, absolute_path, "out_of_scope"
    return True, absolute_path, ""


def _resolve_gitnexus_path(relative_path: str, repo_root: Path) -> Path:
    normalized = relative_path.replace("/", os.sep).replace("\\", os.sep)
    rel_parts = Path(normalized).parts
    root_tail_two = repo_root.parts[-2:] if len(repo_root.parts) >= 2 else repo_root.parts
    trimmed_parts = list(rel_parts)

    if root_tail_two and tuple(rel_parts[: len(root_tail_two)]) == tuple(root_tail_two):
        trimmed_parts = list(rel_parts[len(root_tail_two):])
    elif rel_parts and rel_parts[0].lower() == repo_root.name.lower():
        trimmed_parts = list(rel_parts[1:])

    return repo_root.joinpath(*trimmed_parts).resolve()


def _matches_scope(
    absolute_path: Path,
    symbol_name: str,
    feature: Optional[str],
    url: Optional[str],
    explicit_scope_files: Sequence[Path],
) -> bool:
    if explicit_scope_files:
        for scope_path in explicit_scope_files:
            if scope_path.is_dir() and _is_within_root(absolute_path, scope_path):
                return True
            if absolute_path == scope_path:
                return True

    haystack = f"{absolute_path.as_posix()} {symbol_name}".lower()
    terms = []
    if feature:
        terms.extend(_expand_feature_terms(feature))
    if url:
        terms.extend([url])
        terms.extend([part for part in url.split("/") if part and part not in IGNORED_URL_SEGMENTS])

    if not terms and not explicit_scope_files:
        return False

    for term in terms:
        if term.lower() in haystack:
            return True
    return False


def _is_within_root(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _definition_kind(definition: Dict) -> str:
    raw_id = definition.get("id", "")
    if ":" in raw_id:
        return raw_id.split(":", 1)[0].lower()
    return "definition"


def _add_discovered_file(discovered_files: Dict[str, List[str]], file_path: Path) -> None:
    path_str = str(file_path)
    suffix = file_path.suffix.lower()
    if suffix == ".vue":
        discovered_files["vue_components"].append(path_str)
    elif suffix in (".ts", ".js"):
        discovered_files["api_services"].append(path_str)
    elif suffix == ".xml":
        discovered_files["mybatis_xml"].append(path_str)
    elif suffix == ".java":
        stem = file_path.stem
        if "Controller" in stem:
            discovered_files["controllers"].append(path_str)
        elif "Service" in stem:
            discovered_files["services"].append(path_str)
        elif "Mapper" in stem or "DAO" in stem or "Dao" in stem:
            discovered_files["mappers"].append(path_str)


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    seen = set()
    ordered = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(path)
    return ordered


def _dedupe_strings(values: Iterable[str]) -> List[str]:
    seen = set()
    ordered = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _dedupe_context_files(items: Sequence[Dict]) -> List[Dict]:
    seen = set()
    ordered = []
    for item in items:
        key = (item.get("type"), item.get("path"))
        if key in seen:
            continue
        seen.add(key)
        ordered.append(item)
    return ordered
