from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_audit_event(run_dir: Path, event: dict[str, Any]) -> Path:
    audit_path = run_dir / "audit.jsonl"
    payload = dict(event)
    payload.setdefault("timestamp", _timestamp())
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    return audit_path


def write_stage_artifacts(
    run_dir: Path,
    *,
    stage: str,
    prompt: str,
    response: dict[str, Any],
    decision: dict[str, Any],
) -> None:
    llm_root = run_dir / "llm"
    prompt_path = llm_root / "prompts" / f"{stage}.md"
    response_path = llm_root / "responses" / f"{stage}.json"
    decision_path = llm_root / "decisions" / f"{stage}.json"

    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    response_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)

    prompt_path.write_text(prompt, encoding="utf-8")
    response_path.write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    decision_path.write_text(json.dumps(decision, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    write_audit_event(
        run_dir,
        {
            "event": "llm_prompt",
            "stage": stage,
            "path": str(prompt_path.relative_to(run_dir)),
        },
    )
    write_audit_event(
        run_dir,
        {
            "event": "llm_response",
            "stage": stage,
            "path": str(response_path.relative_to(run_dir)),
        },
    )
    write_audit_event(
        run_dir,
        {
            "event": "llm_decision",
            "stage": stage,
            "path": str(decision_path.relative_to(run_dir)),
        },
    )
