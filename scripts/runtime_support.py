from __future__ import annotations

import json
import sys
import tomllib
from importlib import metadata, resources
from pathlib import Path
from typing import Any


DIST_NAME = "docapi-tools"
DEFAULT_TOOL_VERSION = "0.1.4"
DEFAULT_HOME_DIRNAME = ".docapi"


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _packaged_root() -> Path | None:
    try:
        return Path(str(resources.files("scripts").parent))
    except Exception:
        return None


def resolve_runtime_root() -> Path:
    for candidate in (_repo_root(), _packaged_root()):
        if candidate and (candidate / "assets").exists() and (candidate / "configs").exists():
            return candidate
    return _repo_root()


def resolve_docapi_home() -> Path:
    env_override = None
    try:
        import os

        env_override = os.environ.get("DOCAPI_HOME")
    except Exception:
        env_override = None
    if env_override and str(env_override).strip():
        return Path(env_override).expanduser().resolve()
    return (Path.home() / DEFAULT_HOME_DIRNAME).resolve()


def resolve_asset_path(name: str) -> Path:
    return resolve_runtime_root() / "assets" / name


def resolve_config_path(name: str) -> Path:
    return resolve_runtime_root() / "configs" / name


def resolve_user_config_path(name: str) -> Path:
    return resolve_docapi_home() / name


def installed_version(default: str = DEFAULT_TOOL_VERSION) -> str:
    try:
        return metadata.version(DIST_NAME)
    except metadata.PackageNotFoundError:
        return default


def load_pyproject_version(project_root: str | Path) -> str:
    pyproject_path = Path(project_root).expanduser().resolve() / "pyproject.toml"
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _is_docapi_project_root(path: Path) -> bool:
    pyproject = path / "pyproject.toml"
    if not pyproject.exists():
        return False
    try:
        payload = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except Exception:
        return False
    return str(payload.get("project", {}).get("name", "")).strip() == DIST_NAME


def find_source_project_root(start: str | Path | None = None) -> Path | None:
    root = Path(start or Path.cwd()).expanduser().resolve()
    candidates = [root, *root.parents]
    candidates.extend(child for child in root.iterdir() if child.is_dir())
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if _is_docapi_project_root(candidate):
            return candidate
    return None


def collect_health_report() -> dict[str, Any]:
    runtime_root = resolve_runtime_root()
    docapi_home = resolve_docapi_home()
    checks: list[dict[str, Any]] = []

    def add_check(name: str, path: Path, *, required: bool = True) -> None:
        exists = path.exists()
        checks.append(
            {
                "name": name,
                "path": str(path),
                "required": required,
                "status": "pass" if exists else ("fail" if required else "warn"),
            }
        )

    add_check("runtime_root", runtime_root)
    add_check("api_template", resolve_asset_path("api_template_clean.xlsx"))
    add_check("ui_template", resolve_asset_path("template_clean.xlsx"))
    add_check("project_config", resolve_config_path("project_config.json"))
    add_check("provider_config", resolve_config_path("provider_config.json"))
    checks.append(
        {
            "name": "docapi_home",
            "path": str(docapi_home),
            "required": False,
            "status": "pass",
        }
    )

    status = "pass"
    if any(check["status"] == "fail" for check in checks):
        status = "fail"
    elif any(check["status"] == "warn" for check in checks):
        status = "warn"

    return {
        "status": status,
        "tool_version": installed_version(),
        "python_executable": sys.executable,
        "python_version": sys.version.split()[0],
        "runtime_root": str(runtime_root),
        "docapi_home": str(docapi_home),
        "checks": checks,
    }


def render_health_report(report: dict[str, Any]) -> str:
    lines = [
        f"status: {report['status']}",
        f"version: {report['tool_version']}",
        f"python: {report['python_executable']} ({report['python_version']})",
        f"runtime_root: {report['runtime_root']}",
        "checks:",
    ]
    for check in report["checks"]:
        lines.append(f"- {check['status']}: {check['name']} -> {check['path']}")
    return "\n".join(lines)


def load_json_file(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
