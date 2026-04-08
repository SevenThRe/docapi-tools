#!/usr/bin/env python3
"""
business_glossary.py

UI/API 設計書生成で使う業務用語辞書を SQLite で管理する。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List, Sequence


DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "business_glossary.sqlite"


def normalize_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def ensure_db(db_path: Path) -> Path:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS business_terms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                system_name TEXT NOT NULL DEFAULT '',
                domain TEXT NOT NULL DEFAULT '',
                term_key TEXT NOT NULL,
                normalized_key TEXT NOT NULL,
                display_label TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                description TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'manual',
                source_ref TEXT NOT NULL DEFAULT '',
                confidence REAL NOT NULL DEFAULT 1.0,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_business_terms_unique
            ON business_terms(system_name, domain, normalized_key);
            """
        )
    return db_path


def upsert_term(
    db_path: Path,
    *,
    term_key: str,
    display_label: str,
    system_name: str = "",
    domain: str = "",
    aliases: Sequence[str] = (),
    description: str = "",
    source_type: str = "manual",
    source_ref: str = "",
    confidence: float = 1.0,
) -> None:
    ensure_db(db_path)
    payload = {
        "system_name": system_name or "",
        "domain": domain or "",
        "term_key": term_key,
        "normalized_key": normalize_key(term_key),
        "display_label": display_label,
        "aliases_json": json.dumps(list(dict.fromkeys([alias for alias in aliases if alias])), ensure_ascii=False),
        "description": description or "",
        "source_type": source_type,
        "source_ref": source_ref or "",
        "confidence": confidence,
    }
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO business_terms (
                system_name, domain, term_key, normalized_key, display_label,
                aliases_json, description, source_type, source_ref, confidence
            ) VALUES (
                :system_name, :domain, :term_key, :normalized_key, :display_label,
                :aliases_json, :description, :source_type, :source_ref, :confidence
            )
            ON CONFLICT(system_name, domain, normalized_key)
            DO UPDATE SET
                display_label=excluded.display_label,
                aliases_json=excluded.aliases_json,
                description=excluded.description,
                source_type=excluded.source_type,
                source_ref=excluded.source_ref,
                confidence=excluded.confidence,
                is_active=1,
                updated_at=CURRENT_TIMESTAMP
            """,
            payload,
        )


def collect_labels(
    db_path: Path,
    *,
    system_name: str = "",
    domain_candidates: Sequence[str] = (),
) -> Dict[str, str]:
    if not db_path.exists():
        return {}

    domains = [item for item in dict.fromkeys(domain_candidates) if item]
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT system_name, domain, term_key, normalized_key, display_label, aliases_json
            FROM business_terms
            WHERE is_active = 1
            """
        ).fetchall()

    scored: Dict[str, tuple[int, str]] = {}
    for row in rows:
        score = _score_row(
            row_system=row["system_name"] or "",
            row_domain=row["domain"] or "",
            system_name=system_name or "",
            domains=domains,
        )
        if score < 0:
            continue

        label = row["display_label"]
        keys = [row["term_key"], row["normalized_key"]]
        try:
            aliases = json.loads(row["aliases_json"] or "[]")
        except json.JSONDecodeError:
            aliases = []
        keys.extend(aliases)

        for key in keys:
            normalized = normalize_key(key)
            if not normalized:
                continue
            current = scored.get(normalized)
            if current is None or score > current[0]:
                scored[normalized] = (score, label)

    return {key: value for key, (_, value) in scored.items()}


def import_vue_labels(
    db_path: Path,
    *,
    vue_file: Path,
    system_name: str = "",
    domain: str = "",
) -> int:
    labels = _extract_typescript_field_labels(vue_file.read_text(encoding="utf-8", errors="ignore"))
    for term_key, display_label in labels.items():
        upsert_term(
            db_path,
            system_name=system_name,
            domain=domain,
            term_key=term_key,
            display_label=display_label,
            source_type="vue_comment",
            source_ref=str(vue_file),
        )
    return len(labels)


def lookup_label(
    db_path: Path,
    *,
    term_key: str,
    system_name: str = "",
    domain_candidates: Sequence[str] = (),
) -> str:
    labels = collect_labels(db_path, system_name=system_name, domain_candidates=domain_candidates)
    return labels.get(normalize_key(term_key), "")


def _score_row(*, row_system: str, row_domain: str, system_name: str, domains: Sequence[str]) -> int:
    if row_system and system_name and row_system != system_name:
        return -1
    if row_domain and domains and row_domain not in domains:
        return -1

    score = 0
    if row_system and row_system == system_name:
        score += 20
    if row_domain:
        if row_domain in domains:
            score += 10 + max(0, len(domains) - domains.index(row_domain))
    return score


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

        field_match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\??\s*:", stripped)
        if field_match and pending_label:
            labels.setdefault(field_match.group(1), pending_label)
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


def _pick_comment_label(comment_lines: Iterable[str]) -> str:
    for line in comment_lines:
        if line:
            return line
    return ""


def _default_domain_from_vue(vue_file: Path) -> str:
    return vue_file.stem


def main() -> None:
    parser = argparse.ArgumentParser(description="業務用語 SQLite 辞書を管理する")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="DB を初期化する")
    init_parser.add_argument("--db", default=str(DEFAULT_DB_PATH))

    upsert_parser = subparsers.add_parser("upsert", help="用語を登録/更新する")
    upsert_parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    upsert_parser.add_argument("--system-name", default="")
    upsert_parser.add_argument("--domain", default="")
    upsert_parser.add_argument("--term-key", required=True)
    upsert_parser.add_argument("--label", required=True)
    upsert_parser.add_argument("--alias", action="append", default=[])
    upsert_parser.add_argument("--description", default="")
    upsert_parser.add_argument("--source-type", default="manual")
    upsert_parser.add_argument("--source-ref", default="")

    import_parser = subparsers.add_parser("import-vue", help="Vue コメントから用語を取り込む")
    import_parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    import_parser.add_argument("--system-name", default="")
    import_parser.add_argument("--domain", default="")
    import_parser.add_argument("--vue-file", required=True)

    lookup_parser = subparsers.add_parser("lookup", help="用語を検索する")
    lookup_parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    lookup_parser.add_argument("--system-name", default="")
    lookup_parser.add_argument("--domain", action="append", default=[])
    lookup_parser.add_argument("--term-key", required=True)

    list_parser = subparsers.add_parser("list", help="登録済み用語を一覧する")
    list_parser.add_argument("--db", default=str(DEFAULT_DB_PATH))
    list_parser.add_argument("--system-name", default="")
    list_parser.add_argument("--domain", action="append", default=[])

    args = parser.parse_args()
    db_path = Path(args.db)

    if args.command == "init":
        ensure_db(db_path)
        print(f"[ok] glossary db ready: {db_path}")
        return

    if args.command == "upsert":
        upsert_term(
            db_path,
            system_name=args.system_name,
            domain=args.domain,
            term_key=args.term_key,
            display_label=args.label,
            aliases=args.alias,
            description=args.description,
            source_type=args.source_type,
            source_ref=args.source_ref,
        )
        print(f"[ok] upserted: {args.term_key} -> {args.label}")
        return

    if args.command == "import-vue":
        vue_file = Path(args.vue_file)
        domain = args.domain or _default_domain_from_vue(vue_file)
        count = import_vue_labels(db_path, vue_file=vue_file, system_name=args.system_name, domain=domain)
        print(f"[ok] imported {count} labels from: {vue_file}")
        return

    if args.command == "lookup":
        label = lookup_label(
            db_path,
            term_key=args.term_key,
            system_name=args.system_name,
            domain_candidates=args.domain,
        )
        print(label or "")
        return

    if args.command == "list":
        labels = collect_labels(db_path, system_name=args.system_name, domain_candidates=args.domain)
        for key in sorted(labels):
            print(f"{key}\t{labels[key]}")


if __name__ == "__main__":
    main()
