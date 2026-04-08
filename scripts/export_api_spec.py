from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from scripts.generate_api_spec import ApiSpecGenerator, merge_project_config
from scripts.runtime_support import resolve_user_config_path


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _packaged_root() -> Path | None:
    try:
        return Path(str(resources.files("scripts").parent))
    except Exception:
        return None


def resolve_project_config_path(project_config_path: str | None = None) -> Path | None:
    if project_config_path:
        candidate = Path(project_config_path).expanduser().resolve()
        if candidate.exists():
            return candidate
        raise ValueError(f"Project config not found: {candidate}")

    user_candidate = resolve_user_config_path("project_config.json")
    if user_candidate.exists():
        return user_candidate

    repo_candidate = _repo_root() / "configs" / "project_config.json"
    if repo_candidate.exists():
        return repo_candidate

    packaged_root = _packaged_root()
    if packaged_root:
        packaged_candidate = packaged_root / "configs" / "project_config.json"
        if packaged_candidate.exists():
            return packaged_candidate

    return None


def resolve_template_path(template_path: str | None, project_config: dict[str, Any], project_config_file: Path | None) -> Path:
    if template_path:
        candidate = Path(template_path).expanduser().resolve()
        if candidate.exists():
            return candidate
        raise ValueError(f"Template file not found: {candidate}")

    template_value = project_config.get("template_paths", {}).get("api_spec")
    search_roots: list[Path] = [_repo_root()]
    if project_config_file:
        search_roots.append(project_config_file.parent.parent)

    packaged_root = _packaged_root()
    if packaged_root:
        search_roots.append(packaged_root)

    if template_value:
        for root in search_roots:
            candidate = (root / template_value).resolve()
            if candidate.exists():
                return candidate

    for root in search_roots:
        fallback = (root / "assets" / "api_template_clean.xlsx").resolve()
        if fallback.exists():
            return fallback

    raise ValueError("Unable to resolve API workbook template path.")


def export_api_workbook(
    api_config_path: str,
    *,
    project_config_path: str | None = None,
    template_path: str | None = None,
    output_path: str,
) -> dict[str, str | None]:
    api_config_file = Path(api_config_path).expanduser().resolve()
    if not api_config_file.exists():
        raise ValueError(f"api_config.json not found: {api_config_file}")

    config = json.loads(api_config_file.read_text(encoding="utf-8"))
    project_config_file = resolve_project_config_path(project_config_path)
    project_config: dict[str, Any] = {}
    if project_config_file:
        project_config = json.loads(project_config_file.read_text(encoding="utf-8"))
        config = merge_project_config(config, project_config)

    resolved_template = resolve_template_path(template_path, project_config, project_config_file)

    output_file = Path(output_path).expanduser().resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)

    generator = ApiSpecGenerator(config, str(resolved_template))
    generator.generate(str(output_file))

    return {
        "output_path": str(output_file),
        "template_path": str(resolved_template),
        "project_config_path": str(project_config_file) if project_config_file else None,
    }
