#!/usr/bin/env python3
"""docapi console entrypoint."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence

from scripts.runtime_support import DEFAULT_TOOL_VERSION, collect_health_report, render_health_report


TOOL_NAME = "docapi"
TOOL_VERSION = DEFAULT_TOOL_VERSION
SCAN_ARTIFACT = "scan.json"
ANALYSIS_ARTIFACT = "analysis.json"
API_CONFIG_ARTIFACT = "api_config.json"
QUALITY_ARTIFACT = "quality_report.json"
WORKBOOK_ARTIFACT = "api_spec.xlsx"
EXPORT_ARTIFACT = "export.json"
WORKBOOK_VALIDATION_ARTIFACT = "workbook_validation.json"
REVIEW_ARTIFACT = "review_findings.json"
REPAIR_ARTIFACT = "repair_report.json"
MANIFEST_ARTIFACT = "manifest.json"


@dataclass(frozen=True)
class TargetSpec:
    mode: str
    raw_value: str
    resolved_value: str


@dataclass(frozen=True)
class SelectionResult:
    candidate: dict[str, Any]
    selected_index: int


def package_to_relative_path(package_name: str) -> Path:
    package_name = package_name.strip().strip(".")
    if not package_name:
        raise ValueError("Package name cannot be empty.")
    normalized = package_name.replace("\\", ".").replace("/", ".")
    segments = [segment for segment in normalized.split(".") if segment]
    if not segments:
        raise ValueError(f"Invalid package value: {package_name}")
    return Path(*segments)


def add_common_target_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--api", help="Explicit API path target (e.g. /api/example/show)")
    parser.add_argument("--package", help="Java package target (e.g. jp.co.fminc.socia.aplAprList)")
    parser.add_argument("--path", help="Explicit file or directory target")
    parser.add_argument("--back-root", help="Backend Java source root")
    parser.add_argument("--front-root", help="Frontend source root")
    parser.add_argument("--output-dir", help="Directory for generated artifacts")
    parser.add_argument("--output-json", help="Explicit path for scan.json output")
    parser.add_argument("--pick", help="Candidate index or comma-separated indexes to select")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation prompts")
    parser.add_argument("--non-interactive", action="store_true", help="Disable interactive prompts")
    parser.add_argument(
        "--engine",
        choices=["heuristic", "gitnexus", "hybrid"],
        default="heuristic",
        help="Analysis engine for analyze/generate stages",
    )
    parser.add_argument(
        "--quality-gate",
        choices=["off", "report", "strict"],
        default="report",
        help="Quality gate mode for generated api_config artifacts",
    )
    parser.add_argument(
        "--provider",
        choices=["none", "ollama"],
        default=None,
        help="Optional provider override. Defaults to provider config value.",
    )
    parser.add_argument(
        "--provider-config",
        default=None,
        help="Path to provider_config.json (defaults to bundled config).",
    )
    parser.add_argument("--verbose", action="store_true", help="Print detailed evidence")


def add_run_directory_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True, help="Existing run directory created by docapi generate")


def resolve_target_spec(args: argparse.Namespace) -> TargetSpec:
    provided = [
        ("api", getattr(args, "api", None)),
        ("package", getattr(args, "package", None)),
        ("path", getattr(args, "path", None)),
    ]
    selected = [(mode, value.strip()) for mode, value in provided if value and value.strip()]
    if not selected:
        raise ValueError("One of --api, --package, or --path must be provided.")
    if len(selected) > 1:
        raise ValueError("Only one of --api, --package, or --path may be provided per command.")

    mode, raw_value = selected[0]
    if mode == "package":
        resolved_value = package_to_relative_path(raw_value).as_posix()
    elif mode == "path":
        resolved_value = str(Path(raw_value).expanduser().resolve())
    else:
        resolved_value = raw_value
    return TargetSpec(mode=mode, raw_value=raw_value, resolved_value=resolved_value)


def default_output_json(args: argparse.Namespace) -> Path:
    if getattr(args, "output_json", None):
        return Path(args.output_json).expanduser().resolve()
    if getattr(args, "output_dir", None):
        return Path(args.output_dir).expanduser().resolve() / SCAN_ARTIFACT
    return Path.cwd() / "output" / SCAN_ARTIFACT


def serialize_scan_inputs(args: argparse.Namespace, target: TargetSpec) -> dict[str, Any]:
    return {
        "mode": target.mode,
        "target": target.raw_value,
        "resolved_target": target.resolved_value,
        "pick": getattr(args, "pick", None),
        "yes": bool(getattr(args, "yes", False)),
        "non_interactive": bool(getattr(args, "non_interactive", False)),
        "verbose": bool(getattr(args, "verbose", False)),
        "engine": getattr(args, "engine", "heuristic"),
        "back_root": str(Path(args.back_root).expanduser().resolve()) if getattr(args, "back_root", None) else None,
        "front_root": str(Path(args.front_root).expanduser().resolve()) if getattr(args, "front_root", None) else None,
        "output_dir": str(Path(args.output_dir).expanduser().resolve()) if getattr(args, "output_dir", None) else None,
        "output_json": str(default_output_json(args)),
    }


def print_scan_table(candidates: Sequence[dict[str, Any]], verbose: bool = False) -> None:
    header = f"{'index':>5}  {'method':<6} {'path':<36} {'summary':<28} {'controller':<36} {'confidence':<10}"
    print(header)
    print("-" * len(header))
    for index, candidate in enumerate(candidates, start=1):
        controller = f"{candidate['controller_class']}.{candidate['controller_method']}"
        summary = candidate["summary"].replace("\n", " ")
        print(
            f"{index:>5}  "
            f"{candidate['method']:<6} "
            f"{_truncate(candidate['path'], 36):<36} "
            f"{_truncate(summary, 28):<28} "
            f"{_truncate(controller, 36):<36} "
            f"{candidate['confidence']:<10}"
        )
        if verbose:
            evidence = json.dumps(candidate.get("evidence", {}), ensure_ascii=False, sort_keys=True)
            print(f"         evidence: {evidence}")
            if candidate.get("warnings"):
                print(f"         warnings: {', '.join(candidate['warnings'])}")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + os.linesep, encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def iso_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sanitize_api_id(api_id: str) -> str:
    sanitized = re.sub(r"[^0-9A-Za-z]+", "_", api_id).strip("_").lower()
    return sanitized or "api"


def parse_pick_indexes(pick_value: Optional[str], *, max_index: int) -> list[int]:
    if pick_value is None:
        return []

    indexes: list[int] = []
    seen: set[int] = set()
    for chunk in pick_value.split(","):
        token = chunk.strip()
        if not token:
            continue
        if not token.isdigit():
            raise ValueError(f"Invalid selection value: {token}")
        selected = int(token)
        if selected < 1 or selected > max_index:
            raise ValueError(f"Selection index out of range: {selected}")
        if selected in seen:
            continue
        seen.add(selected)
        indexes.append(selected)

    if not indexes:
        raise ValueError("Selection cannot be empty.")
    return indexes


def confirm_single_api_target(
    *,
    args: argparse.Namespace,
    target: TargetSpec,
    candidate: dict[str, Any],
) -> None:
    if args.yes or target.mode != "api":
        return

    prompt = (
        f"Generate artifacts for {candidate['method']} {candidate['path']} "
        f"from {candidate['controller_class']}.{candidate['controller_method']}? [y/N]: "
    )
    if args.non_interactive:
        raise ValueError("Use --yes to confirm generation when --api resolves to a single candidate.")

    response = input(prompt).strip().lower()
    if response not in {"y", "yes"}:
        raise ValueError("Selection cancelled by user.")


def select_candidates(
    *,
    args: argparse.Namespace,
    target: TargetSpec,
    candidates: Sequence[dict[str, Any]],
) -> list[SelectionResult]:
    max_index = len(candidates)
    explicit_indexes = parse_pick_indexes(args.pick, max_index=max_index) if args.pick else []

    if explicit_indexes:
        indexes = explicit_indexes
    elif args.non_interactive:
        if max_index > 1:
            raise ValueError("Use --pick with --non-interactive when multiple candidates are discovered.")
        indexes = [1]
    elif max_index == 1:
        indexes = [1]
    else:
        print("")
        selection_raw = input("Select candidate index (comma-separated for multiple, default 1): ").strip()
        indexes = parse_pick_indexes(selection_raw or "1", max_index=max_index)

    if max_index == 1 and indexes == [1]:
        confirm_single_api_target(args=args, target=target, candidate=candidates[0])

    return [
        SelectionResult(candidate=candidates[index - 1], selected_index=index)
        for index in indexes
    ]


def resolve_output_root(args: argparse.Namespace) -> Path:
    if getattr(args, "output_dir", None):
        return Path(args.output_dir).expanduser().resolve()
    return (Path.cwd() / "output").resolve()


def infer_project_root(back_root: Path) -> Path:
    path = back_root.resolve()
    if path.name == "java" and path.parent.name == "main" and path.parent.parent.name == "src":
        return path.parent.parent.parent
    for candidate in [path] + list(path.parents):
        if (candidate / "src" / "main" / "java").exists():
            return candidate
    return path


def resolve_analysis_project_root(back_root: Path, front_root: Optional[Path]) -> Path:
    inferred_back_project = infer_project_root(back_root)
    if not front_root:
        return inferred_back_project
    common_root = Path(os.path.commonpath([str(inferred_back_project), str(front_root.resolve())]))
    return common_root


def candidate_metadata(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": candidate["id"],
        "method": candidate["method"],
        "path": candidate["path"],
        "summary": candidate["summary"],
        "controller_class": candidate["controller_class"],
        "controller_method": candidate["controller_method"],
        "confidence": candidate["confidence"],
    }


def order_selection_candidates(candidates: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    confidence_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(
        candidates,
        key=lambda candidate: (
            confidence_rank.get(candidate.get("confidence", "LOW"), 9),
            candidate["path"],
            candidate["method"],
            candidate["controller_class"],
            candidate["controller_method"],
        ),
    )


def build_run_directory_name(timestamp: str, candidate: dict[str, Any]) -> str:
    file_timestamp = timestamp.replace("-", "").replace(":", "").replace("T", "-").replace("Z", "")
    return f"{file_timestamp}_{sanitize_api_id(candidate['id'])}"


def build_manifest(
    *,
    args: argparse.Namespace,
    target: TargetSpec,
    selection: SelectionResult,
    started_at: str,
    completed_at: str,
    artifacts: dict[str, str],
    warnings: Sequence[dict[str, Any]],
    provider: dict[str, Any] | None = None,
    pipeline: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    payload = {
        "input_mode": target.mode,
        "selected_api": candidate_metadata(selection.candidate),
        "selected_index": selection.selected_index,
        "tool_version": TOOL_VERSION,
        "started_at": started_at,
        "completed_at": completed_at,
        "artifacts": dict(artifacts),
        "warnings": list(warnings),
        "engine": args.engine,
    }
    if provider is not None:
        payload["provider"] = provider
    if pipeline is not None:
        payload["pipeline"] = list(pipeline)
    return payload


def build_analysis_payload(
    *,
    selection: SelectionResult,
    analysis: dict[str, Any],
    generated_at: str,
    project_root: Path,
    back_root: Path,
    front_root: Optional[Path],
    engine: str,
) -> dict[str, Any]:
    return {
        "selected_api": {
            **candidate_metadata(selection.candidate),
            "selected_index": selection.selected_index,
        },
        "generated_at": generated_at,
        "tool_version": TOOL_VERSION,
        "evidence_roots": {
            "project": str(project_root),
            "back_root": str(back_root),
            "front_root": str(front_root) if front_root else None,
            "engine": engine,
        },
        "analysis": analysis,
    }


def resolve_project_config_path() -> Path | None:
    from scripts.runtime_support import resolve_config_path

    candidate = resolve_config_path("project_config.json")
    if candidate.exists():
        return candidate
    return None


def prepare_scan_and_selections(
    args: argparse.Namespace,
) -> tuple[TargetSpec, dict[str, Any], list[SelectionResult], Path, Path | None, Path]:
    from scripts.extract_api_inventory import build_scan_artifact

    target = resolve_target_spec(args)
    scan_artifact = build_scan_artifact(
        target_mode=target.mode,
        target_value=target.raw_value,
        resolved_target=target.resolved_value,
        back_root=args.back_root,
        front_root=args.front_root,
        output_json=str(default_output_json(args)),
        verbose=args.verbose,
    )
    ordered_candidates = order_selection_candidates(scan_artifact["candidates"])
    print_scan_table(ordered_candidates, verbose=args.verbose)
    if scan_artifact["warnings"]:
        print("")
        print("Warnings:")
        for warning in scan_artifact["warnings"]:
            print(f"- {warning['message']}")

    selections = select_candidates(args=args, target=target, candidates=ordered_candidates)
    back_root = Path(scan_artifact["scan"]["roots"]["back_root"]).resolve()
    front_root_value = scan_artifact["scan"]["roots"].get("front_root")
    front_root = Path(front_root_value).resolve() if front_root_value else None
    project_root = resolve_analysis_project_root(back_root, front_root)
    return target, scan_artifact, selections, back_root, front_root, project_root


def run_analysis_for_selection(
    *,
    args: argparse.Namespace,
    selection: SelectionResult,
    project_root: Path,
    back_root: Path,
    front_root: Path | None,
) -> dict[str, Any]:
    from scripts.analyze_code import run_analysis

    analysis_result = run_analysis(
        project=str(project_root),
        engine=args.engine,
        url=selection.candidate["path"],
        front_root=str(front_root) if front_root else None,
        back_root=str(back_root),
    )
    generated_at = iso_timestamp()
    return build_analysis_payload(
        selection=selection,
        analysis=analysis_result,
        generated_at=generated_at,
        project_root=project_root,
        back_root=back_root,
        front_root=front_root,
        engine=args.engine,
    )


def build_validated_api_config(analysis_payload: dict[str, Any]) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    from scripts.api_config_schema import validate_api_config
    from scripts.api_quality_gate import evaluate_api_quality
    from scripts.build_api_config_from_analysis import build_api_config

    project_config_path = resolve_project_config_path()
    project_config = load_json(project_config_path) if project_config_path else {}
    api_config = build_api_config(analysis_payload, project_config)
    errors, warnings = validate_api_config(api_config)
    if errors:
        raise ValueError("Generated api_config.json is invalid: " + "; ".join(errors))
    quality_report = evaluate_api_quality(api_config, analysis_payload)
    return api_config, warnings, quality_report


def resolve_provider_settings(args: argparse.Namespace) -> dict[str, Any]:
    from scripts.provider_config import load_provider_config

    provider_config = load_provider_config(getattr(args, "provider_config", None))
    cli_provider = getattr(args, "provider", None)
    effective_provider = cli_provider if cli_provider is not None else provider_config.get("provider", "none")
    effective_provider = str(effective_provider).strip().lower()
    if effective_provider not in {"none", "ollama"}:
        raise ValueError(f"Unsupported provider '{effective_provider}'.")

    settings = {
        "name": effective_provider,
        "config_path": provider_config.get("config_path"),
    }
    if effective_provider == "ollama":
        ollama = provider_config.get("ollama", {})
        settings["ollama"] = {
            "base_url": str(ollama.get("base_url", "http://127.0.0.1:11434")),
            "model": str(ollama.get("model", "llama3.1")),
            "timeout_sec": int(ollama.get("timeout_sec", 30)),
        }
    return settings


def _build_provider_prompt(selection: SelectionResult, analysis_payload: dict[str, Any]) -> str:
    llm_files = analysis_payload.get("analysis", {}).get("llm_context_files", [])
    entries: list[str] = []
    for item in llm_files[:20]:
        path = str(item.get("path", "")).strip()
        item_type = str(item.get("type", "context")).strip()
        priority = item.get("priority")
        short_name = Path(path).name if path else "(unknown)"
        entries.append(f"- [{item_type}] {short_name} (priority={priority})")

    if not entries:
        entries.append("- (no llm_context_files discovered)")

    return (
        "You are reviewing a deterministic API documentation draft.\n"
        "Return concise improvement suggestions only; do not include markdown code fences.\n\n"
        f"API: {selection.candidate['method']} {selection.candidate['path']}\n"
        f"Summary: {selection.candidate['summary']}\n\n"
        "High-priority context files:\n"
        + "\n".join(entries)
    )


def run_provider_stage(
    *,
    run_dir: Path,
    selection: SelectionResult,
    analysis_payload: dict[str, Any],
    provider_settings: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    from scripts.provider_audit import write_audit_event, write_stage_artifacts
    from scripts.providers.ollama_provider import OllamaProvider

    if provider_settings["name"] == "none":
        return [{"name": "provider", "status": "skipped", "reason": "provider=none"}], None

    ollama_settings = provider_settings["ollama"]
    prompt = _build_provider_prompt(selection, analysis_payload)
    provider = OllamaProvider(
        base_url=ollama_settings["base_url"],
        model=ollama_settings["model"],
        timeout_sec=ollama_settings["timeout_sec"],
    )
    stage_name = "draft_enhancement"
    write_audit_event(run_dir, {"event": "provider_start", "provider": "ollama", "stage": stage_name})
    try:
        response = provider.generate_text(prompt)
    except RuntimeError as exc:
        decision = {"applied": False, "reason": "provider_error", "error": str(exc)}
        write_stage_artifacts(
            run_dir,
            stage=stage_name,
            prompt=prompt,
            response={"error": str(exc)},
            decision=decision,
        )
        write_audit_event(run_dir, {"event": "provider_error", "provider": "ollama", "error": str(exc)})
        raise ValueError(f"Ollama provider failed: {exc}") from exc

    decision = {
        "applied": False,
        "reason": "Phase 2 keeps deterministic api_config output and records advisory response only.",
    }
    write_stage_artifacts(
        run_dir,
        stage=stage_name,
        prompt=prompt,
        response=response,
        decision=decision,
    )
    write_audit_event(run_dir, {"event": "provider_complete", "provider": "ollama", "stage": stage_name})
    pipeline_steps = [{"name": "provider", "status": "completed", "provider": "ollama"}]
    return pipeline_steps, {
        "prompt": f"llm/prompts/{stage_name}.md",
        "response": f"llm/responses/{stage_name}.json",
        "decision": f"llm/decisions/{stage_name}.json",
        "audit": "audit.jsonl",
    }


def run_scan(args: argparse.Namespace) -> int:
    from scripts.extract_api_inventory import build_scan_artifact

    target = resolve_target_spec(args)
    serialize_scan_inputs(args, target)
    artifact = build_scan_artifact(
        target_mode=target.mode,
        target_value=target.raw_value,
        resolved_target=target.resolved_value,
        back_root=args.back_root,
        front_root=args.front_root,
        output_json=str(default_output_json(args)),
        verbose=args.verbose,
    )
    print_scan_table(artifact["candidates"], verbose=args.verbose)
    if artifact["warnings"]:
        print("")
        print("Warnings:")
        for warning in artifact["warnings"]:
            print(f"- {warning['message']}")

    output_path = Path(artifact["output_json"])
    write_json(output_path, artifact["scan"])
    print("")
    print(f"{SCAN_ARTIFACT}: {output_path}")
    return 0


def run_analysis_stage(args: argparse.Namespace) -> int:
    target, scan_artifact, selections, back_root, front_root, project_root = prepare_scan_and_selections(args)
    output_root = resolve_output_root(args)
    output_root.mkdir(parents=True, exist_ok=True)

    command_name = getattr(args, "command_name", "analyze")
    for selection in selections:
        started_at = iso_timestamp()
        run_dir = output_root / build_run_directory_name(started_at, selection.candidate)
        run_dir.mkdir(parents=True, exist_ok=False)

        write_json(run_dir / SCAN_ARTIFACT, scan_artifact["scan"])

        analysis_payload = run_analysis_for_selection(
            args=args,
            selection=selection,
            project_root=project_root,
            back_root=back_root,
            front_root=front_root,
        )
        write_json(run_dir / ANALYSIS_ARTIFACT, analysis_payload)
        completed_at = iso_timestamp()

        manifest_payload = build_manifest(
            args=args,
            target=target,
            selection=selection,
            started_at=started_at,
            completed_at=completed_at,
            artifacts={
                "scan": SCAN_ARTIFACT,
                "analysis": ANALYSIS_ARTIFACT,
            },
            warnings=scan_artifact["warnings"],
        )
        write_json(run_dir / MANIFEST_ARTIFACT, manifest_payload)

        print("")
        print(f"{command_name} run: {run_dir}")
        print(f"- {MANIFEST_ARTIFACT}: {run_dir / MANIFEST_ARTIFACT}")
        print(f"- {SCAN_ARTIFACT}: {run_dir / SCAN_ARTIFACT}")
        print(f"- {ANALYSIS_ARTIFACT}: {run_dir / ANALYSIS_ARTIFACT}")
    return 0


def run_draft_stage(args: argparse.Namespace) -> int:
    from scripts.api_quality_gate import enforce_quality_gate
    from scripts.export_api_spec import export_api_workbook
    from scripts.generate_api_spec import build_default_output_name
    from scripts.validate_api_workbook import validate_api_workbook

    target, scan_artifact, selections, back_root, front_root, project_root = prepare_scan_and_selections(args)
    output_root = resolve_output_root(args)
    output_root.mkdir(parents=True, exist_ok=True)
    provider_settings = resolve_provider_settings(args)

    command_name = getattr(args, "command_name", "draft")
    for selection in selections:
        started_at = iso_timestamp()
        run_dir = output_root / build_run_directory_name(started_at, selection.candidate)
        run_dir.mkdir(parents=True, exist_ok=False)
        pipeline_steps: list[dict[str, Any]] = []

        write_json(run_dir / SCAN_ARTIFACT, scan_artifact["scan"])
        pipeline_steps.append({"name": "scan", "status": "completed"})
        analysis_payload = run_analysis_for_selection(
            args=args,
            selection=selection,
            project_root=project_root,
            back_root=back_root,
            front_root=front_root,
        )
        write_json(run_dir / ANALYSIS_ARTIFACT, analysis_payload)
        pipeline_steps.append({"name": "analysis", "status": "completed"})

        api_config, validation_warnings, quality_report = build_validated_api_config(analysis_payload)
        write_json(run_dir / API_CONFIG_ARTIFACT, api_config)
        write_json(run_dir / QUALITY_ARTIFACT, quality_report)
        quality_warnings = enforce_quality_gate(quality_report, mode=args.quality_gate)
        pipeline_steps.append({"name": "draft", "status": "completed"})
        pipeline_steps.append({"name": "quality_gate", "status": "completed", "mode": args.quality_gate})

        artifacts = {
            "scan": SCAN_ARTIFACT,
            "analysis": ANALYSIS_ARTIFACT,
            "api_config": API_CONFIG_ARTIFACT,
            "quality_report": QUALITY_ARTIFACT,
        }
        export_warnings: list[str] = []

        if command_name == "generate":
            provider_steps, provider_artifacts = run_provider_stage(
                run_dir=run_dir,
                selection=selection,
                analysis_payload=analysis_payload,
                provider_settings=provider_settings,
            )
            pipeline_steps.extend(provider_steps)
            if provider_artifacts:
                artifacts.update(
                    {
                        "llm_prompt": provider_artifacts["prompt"],
                        "llm_response": provider_artifacts["response"],
                        "llm_decision": provider_artifacts["decision"],
                        "audit": provider_artifacts["audit"],
                    }
                )

            export_started = iso_timestamp()
            try:
                export_meta = export_api_workbook(
                    str(run_dir / API_CONFIG_ARTIFACT),
                    output_path=str(run_dir / WORKBOOK_ARTIFACT),
                )
                workbook_validation = validate_api_workbook(
                    run_dir / WORKBOOK_ARTIFACT,
                    api_config=api_config,
                    analysis_payload=analysis_payload,
                )
                write_json(run_dir / WORKBOOK_VALIDATION_ARTIFACT, workbook_validation)
                published_name = Path(build_default_output_name(api_config)).name
                published_path = output_root / published_name
                shutil.copy2(run_dir / WORKBOOK_ARTIFACT, published_path)
                export_completed = iso_timestamp()
                export_payload = {
                    **export_meta,
                    "published_output_path": str(published_path),
                    "status": "completed",
                    "started_at": export_started,
                    "completed_at": export_completed,
                }
                write_json(run_dir / EXPORT_ARTIFACT, export_payload)
                artifacts["api_spec"] = WORKBOOK_ARTIFACT
                artifacts["export"] = EXPORT_ARTIFACT
                artifacts["workbook_validation"] = WORKBOOK_VALIDATION_ARTIFACT
                artifacts["published_api_spec"] = published_name
                pipeline_steps.append({"name": "export", "status": "completed"})
                pipeline_steps.append({"name": "workbook_validation", "status": workbook_validation["status"]})
            except Exception as exc:  # keep multi-pick runs alive even if one export fails
                export_completed = iso_timestamp()
                export_error = str(exc)
                export_payload = {
                    "status": "failed",
                    "error": export_error,
                    "started_at": export_started,
                    "completed_at": export_completed,
                }
                write_json(run_dir / EXPORT_ARTIFACT, export_payload)
                artifacts["export"] = EXPORT_ARTIFACT
                pipeline_steps.append({"name": "export", "status": "failed", "error": export_error})
                export_warnings.append(export_error)

        completed_at = iso_timestamp()

        manifest_warnings = list(scan_artifact["warnings"])
        manifest_warnings.extend(
            {
                "code": "api_config_warning",
                "message": warning,
            }
            for warning in validation_warnings
        )
        manifest_warnings.extend(
            {
                "code": "quality_warning",
                "message": warning,
            }
            for warning in quality_warnings
        )
        manifest_warnings.extend(
            {
                "code": "export_warning",
                "message": warning,
            }
            for warning in export_warnings
        )
        manifest_payload = build_manifest(
            args=args,
            target=target,
            selection=selection,
            started_at=started_at,
            completed_at=completed_at,
            artifacts=artifacts,
            warnings=manifest_warnings,
            provider=provider_settings,
            pipeline=pipeline_steps,
        )
        write_json(run_dir / MANIFEST_ARTIFACT, manifest_payload)

        print("")
        print(f"{command_name} run: {run_dir}")
        print(f"- {MANIFEST_ARTIFACT}: {run_dir / MANIFEST_ARTIFACT}")
        print(f"- {SCAN_ARTIFACT}: {run_dir / SCAN_ARTIFACT}")
        print(f"- {ANALYSIS_ARTIFACT}: {run_dir / ANALYSIS_ARTIFACT}")
        print(f"- {API_CONFIG_ARTIFACT}: {run_dir / API_CONFIG_ARTIFACT}")
        print(f"- {QUALITY_ARTIFACT}: {run_dir / QUALITY_ARTIFACT}")
        if command_name == "generate":
            print(f"- {WORKBOOK_ARTIFACT}: {run_dir / WORKBOOK_ARTIFACT}")
            print(f"- {EXPORT_ARTIFACT}: {run_dir / EXPORT_ARTIFACT}")
            if (run_dir / WORKBOOK_VALIDATION_ARTIFACT).exists():
                print(f"- {WORKBOOK_VALIDATION_ARTIFACT}: {run_dir / WORKBOOK_VALIDATION_ARTIFACT}")
    return 0


def run_review_stage(args: argparse.Namespace) -> int:
    from scripts.review_api_run import review_api_run

    run_dir = Path(args.run_dir).expanduser().resolve()
    report = review_api_run(run_dir)
    report_path = run_dir / REVIEW_ARTIFACT
    write_json(report_path, report)
    print(f"{REVIEW_ARTIFACT}: {report_path}")
    print(f"status={report['status']} findings={report['summary']['findings']}")
    return 0


def run_repair_stage(args: argparse.Namespace) -> int:
    from scripts.repair_api_run import repair_api_run
    from scripts.review_api_run import review_api_run
    from scripts.validate_api_workbook import validate_api_workbook

    run_dir = Path(args.run_dir).expanduser().resolve()
    review_path = run_dir / REVIEW_ARTIFACT
    if not review_path.exists():
        review_report = review_api_run(run_dir)
        write_json(review_path, review_report)

    repair_report = repair_api_run(run_dir, findings_path=review_path)
    repair_path = run_dir / REPAIR_ARTIFACT
    write_json(repair_path, repair_report)

    repaired_workbook_path = Path(repair_report["repaired_workbook"])
    workbook_validation = validate_api_workbook(
        repaired_workbook_path,
        api_config=load_json(run_dir / API_CONFIG_ARTIFACT),
        analysis_payload=load_json(run_dir / ANALYSIS_ARTIFACT) if (run_dir / ANALYSIS_ARTIFACT).exists() else {},
    )
    write_json(run_dir / WORKBOOK_VALIDATION_ARTIFACT, workbook_validation)

    manifest_path = run_dir / MANIFEST_ARTIFACT
    if manifest_path.exists():
        manifest = load_json(manifest_path)
        artifacts = manifest.setdefault("artifacts", {})
        artifacts["review_findings"] = REVIEW_ARTIFACT
        artifacts["repair_report"] = REPAIR_ARTIFACT
        artifacts["api_spec_repaired"] = repaired_workbook_path.name
        artifacts["workbook_validation"] = WORKBOOK_VALIDATION_ARTIFACT
        manifest.setdefault("pipeline", []).append({"name": "repair", "status": "completed"})
        write_json(manifest_path, manifest)

    print(f"{REPAIR_ARTIFACT}: {repair_path}")
    print(f"repaired_workbook: {repaired_workbook_path}")
    return 0


def run_health_stage(args: argparse.Namespace) -> int:
    report = collect_health_report()
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_health_report(report))
    return 0 if report["status"] != "fail" else 1


def run_self_update_stage(args: argparse.Namespace) -> int:
    from scripts.release_tools import build_update_report, load_release_manifest

    current_version = TOOL_VERSION
    payload: dict[str, Any] = {
        "current_version": current_version,
        "status": "noop",
    }

    if args.spec:
        payload.update(
            {
                "status": "ready",
                "install_spec": args.spec,
                "target_version": None,
                "needs_update": True,
            }
        )
    elif args.manifest:
        manifest, manifest_source = load_release_manifest(args.manifest)
        update_report = build_update_report(current_version, manifest, manifest_source=manifest_source)
        payload.update(
            {
                "status": "ready" if update_report["needs_update"] else "up-to-date",
                "target_version": update_report["latest_version"],
                "needs_update": update_report["needs_update"],
                "install_spec": update_report["install_spec"],
                "manifest_source": manifest_source,
            }
        )
        if args.check:
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                if payload["needs_update"]:
                    print(f"Update available: {current_version} -> {payload['target_version']}")
                    print(f"install_spec: {payload['install_spec']}")
                else:
                    print(f"Already up to date: {current_version}")
            return 0
        if not payload["needs_update"]:
            if getattr(args, "json", False):
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(f"Already up to date: {current_version}")
            return 0
    else:
        raise ValueError("Use --spec or --manifest with self-update.")

    command = [sys.executable, "-m", "pip", "install", "--upgrade", str(payload["install_spec"])]
    payload["command"] = command

    if args.dry_run:
        payload["status"] = "dry-run"
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print("Dry run:")
            print(" ".join(command))
        return 0

    result = subprocess.run(command, capture_output=True, text=True, check=False)
    payload["returncode"] = result.returncode
    payload["stdout"] = result.stdout
    payload["stderr"] = result.stderr
    payload["status"] = "completed" if result.returncode == 0 else "failed"

    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"self-update status: {payload['status']}")
        if payload.get("target_version"):
            print(f"target_version: {payload['target_version']}")
        print(f"install_spec: {payload['install_spec']}")
    return result.returncode


def run_release_stage(args: argparse.Namespace) -> int:
    from scripts.release_tools import build_release_artifacts

    report = build_release_artifacts(
        output_dir=args.output_dir,
        project_root=getattr(args, "project_root", None),
        base_url=getattr(args, "base_url", None),
        python_executable=getattr(args, "python_executable", None),
    )
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"release version: {report['version']}")
        print(f"wheel: {report['wheel_path']}")
        print(f"manifest: {report['manifest_path']}")
        print(f"install script: {report['install_script']}")
        print(f"update script: {report['update_script']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=TOOL_NAME, description="Deterministic API documentation tooling.")
    parser.add_argument("--version", action="version", version=f"%(prog)s {TOOL_VERSION}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Discover candidate APIs for an explicit backend target")
    add_common_target_arguments(scan_parser)
    scan_parser.set_defaults(handler=run_scan, command_name="scan")

    review_parser = subparsers.add_parser("review", help="Review a generated run directory and emit structured findings")
    add_run_directory_argument(review_parser)
    review_parser.set_defaults(handler=run_review_stage, command_name="review")

    repair_parser = subparsers.add_parser("repair", help="Apply safe config-level fixes from review findings")
    add_run_directory_argument(repair_parser)
    repair_parser.set_defaults(handler=run_repair_stage, command_name="repair")

    health_parser = subparsers.add_parser("health", help="Verify packaged assets, config, and runtime health")
    health_parser.add_argument("--json", action="store_true", help="Render the health report as JSON")
    health_parser.set_defaults(handler=run_health_stage, command_name="health")

    self_update_parser = subparsers.add_parser("self-update", help="Check for or apply a CLI update")
    self_update_parser.add_argument("--manifest", default=None, help="Release manifest path or URL")
    self_update_parser.add_argument("--spec", default=None, help="Explicit pip install spec to upgrade to")
    self_update_parser.add_argument("--check", action="store_true", help="Only check whether an update is available")
    self_update_parser.add_argument("--dry-run", action="store_true", help="Print the update command without executing it")
    self_update_parser.add_argument("--json", action="store_true", help="Render the update result as JSON")
    self_update_parser.set_defaults(handler=run_self_update_stage, command_name="self-update")

    release_parser = subparsers.add_parser("release", help="Build release artifacts for install/update distribution")
    release_parser.add_argument("--output-dir", default="dist/release", help="Directory for wheel, manifest, and installer scripts")
    release_parser.add_argument("--project-root", default=None, help="Explicit docapi source project root")
    release_parser.add_argument("--base-url", default=None, help="Published base URL for generated install/update metadata")
    release_parser.add_argument("--python-executable", default=None, help="Python executable to use for wheel building")
    release_parser.add_argument("--json", action="store_true", help="Render the release report as JSON")
    release_parser.set_defaults(handler=run_release_stage, command_name="release")

    for name, help_text in (
        ("analyze", "Analyze a selected API target"),
        ("draft", "Create a deterministic api_config draft"),
        ("generate", "Run the end-to-end API documentation flow"),
    ):
        subparser = subparsers.add_parser(name, help=help_text)
        add_common_target_arguments(subparser)
        if name in {"draft", "generate"}:
            subparser.set_defaults(handler=run_draft_stage, command_name=name)
        else:
            subparser.set_defaults(handler=run_analysis_stage, command_name=name)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    if width <= 3:
        return value[:width]
    return value[: width - 3] + "..."


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
