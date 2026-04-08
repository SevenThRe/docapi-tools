from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen

from scripts.runtime_support import find_source_project_root, load_pyproject_version


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _version_tuple(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in value.split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits or "0"))
    return tuple(parts)


def compare_versions(left: str, right: str) -> int:
    a = _version_tuple(left)
    b = _version_tuple(right)
    max_len = max(len(a), len(b))
    for index in range(max_len):
        av = a[index] if index < len(a) else 0
        bv = b[index] if index < len(b) else 0
        if av != bv:
            return -1 if av < bv else 1
    return 0


def _is_url(value: str) -> bool:
    scheme = urlparse(value).scheme
    return scheme in {"http", "https"}


def load_release_manifest(source: str | Path) -> tuple[dict[str, Any], str]:
    if isinstance(source, Path):
        source = str(source)

    if _is_url(source):
        with urlopen(source, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return payload, source

    manifest_path = Path(source).expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    return payload, str(manifest_path)


def resolve_install_spec(manifest: dict[str, Any], manifest_source: str | None = None) -> str:
    explicit = str(manifest.get("install_spec") or "").strip()
    if explicit:
        return explicit

    wheel_name = str(manifest.get("wheel") or "").strip()
    if not wheel_name:
        raise ValueError("Release manifest is missing both `install_spec` and `wheel`.")

    base_url = str(manifest.get("base_url") or "").strip()
    if base_url:
        return urljoin(base_url.rstrip("/") + "/", wheel_name)

    if not manifest_source:
        return wheel_name

    if _is_url(manifest_source):
        return urljoin(manifest_source, wheel_name)

    return str(Path(manifest_source).resolve().parent / wheel_name)


def build_update_report(current_version: str, manifest: dict[str, Any], manifest_source: str | None = None) -> dict[str, Any]:
    latest_version = str(manifest["version"])
    install_spec = resolve_install_spec(manifest, manifest_source=manifest_source)
    needs_update = compare_versions(current_version, latest_version) < 0
    return {
        "current_version": current_version,
        "latest_version": latest_version,
        "needs_update": needs_update,
        "install_spec": install_spec,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _render_install_script(manifest_filename: str, embedded_manifest_url: str | None, *, upgrade: bool) -> str:
    manifest_url = embedded_manifest_url or ""
    upgrade_literal = "$true" if upgrade else "$false"
    return f"""param(
[string]$ManifestUrl = "{manifest_url}",
[string]$Spec = ""
)

$ErrorActionPreference = "Stop"
$upgrade = {upgrade_literal}
$python = (Get-Command python -ErrorAction Stop).Source
$scriptDir = if ($MyInvocation.MyCommand.Path) {{ Split-Path -Parent $MyInvocation.MyCommand.Path }} else {{ $PWD.Path }}

if (-not $Spec) {{
  $manifestPath = Join-Path $scriptDir "{manifest_filename}"
  if (Test-Path $manifestPath) {{
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json
  }} elseif ($ManifestUrl) {{
    $manifest = Invoke-RestMethod -UseBasicParsing $ManifestUrl
  }} else {{
    throw "No release manifest was provided."
  }}

  if ($manifest.install_spec) {{
    $Spec = [string]$manifest.install_spec
  }} elseif ($manifest.wheel) {{
    if ($ManifestUrl) {{
      $manifestUri = [System.Uri]$ManifestUrl
      $baseUri = New-Object System.Uri($manifestUri, "./")
      $Spec = (New-Object System.Uri($baseUri, [string]$manifest.wheel)).AbsoluteUri
    }} else {{
      $Spec = Join-Path $scriptDir ([string]$manifest.wheel)
    }}
  }} else {{
    throw "Release manifest is missing install_spec/wheel."
  }}
}}

$args = @("-m", "pip", "install")
if ($upgrade) {{
  $args += "--upgrade"
}}
$args += $Spec

& $python @args
"""


def _render_release_notes(manifest: dict[str, Any]) -> str:
    install_hint = manifest.get("install_spec") or f".\\{manifest['wheel']}"
    update_hint = manifest.get("manifest_url") or ".\\release-manifest.json"
    return (
        f"docapi release {manifest['version']}\n\n"
        "Install examples:\n"
        f"- pip install {install_hint}\n"
        f"- powershell -ExecutionPolicy Bypass -File .\\install-docapi.ps1\n\n"
        "Update examples:\n"
        f"- docapi self-update --manifest {update_hint}\n"
        f"- powershell -ExecutionPolicy Bypass -File .\\update-docapi.ps1\n"
    )


def build_release_artifacts(
    *,
    output_dir: str | Path,
    project_root: str | Path | None = None,
    base_url: str | None = None,
    python_executable: str | None = None,
) -> dict[str, Any]:
    resolved_project_root = Path(project_root).expanduser().resolve() if project_root else find_source_project_root()
    if resolved_project_root is None:
        raise ValueError("Unable to locate docapi source project root. Run release from the repo or pass --project-root.")

    release_output_dir = Path(output_dir).expanduser().resolve()
    release_output_dir.mkdir(parents=True, exist_ok=True)

    python_bin = python_executable or sys.executable
    subprocess.run(
        [python_bin, "-m", "pip", "wheel", str(resolved_project_root), "--no-deps", "-w", str(release_output_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    version = load_pyproject_version(resolved_project_root)
    wheel_files = sorted(release_output_dir.glob(f"docapi_tools-{version}-*.whl"))
    if not wheel_files:
        raise ValueError(f"No wheel was created for version {version}.")
    wheel_path = wheel_files[-1]

    normalized_base_url = base_url.rstrip("/") + "/" if base_url else None
    manifest = {
        "name": "docapi-tools",
        "version": version,
        "generated_at": _timestamp(),
        "wheel": wheel_path.name,
        "sha256": _sha256(wheel_path),
        "base_url": normalized_base_url,
        "install_spec": urljoin(normalized_base_url, wheel_path.name) if normalized_base_url else None,
        "manifest_url": urljoin(normalized_base_url, "release-manifest.json") if normalized_base_url else None,
    }

    manifest_path = release_output_dir / "release-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    install_script_path = release_output_dir / "install-docapi.ps1"
    install_script_path.write_text(
        _render_install_script("release-manifest.json", manifest["manifest_url"], upgrade=False),
        encoding="utf-8",
    )

    update_script_path = release_output_dir / "update-docapi.ps1"
    update_script_path.write_text(
        _render_install_script("release-manifest.json", manifest["manifest_url"], upgrade=True),
        encoding="utf-8",
    )

    notes_path = release_output_dir / "README.txt"
    notes_path.write_text(_render_release_notes(manifest), encoding="utf-8")

    return {
        "status": "completed",
        "project_root": str(resolved_project_root),
        "output_dir": str(release_output_dir),
        "manifest_path": str(manifest_path),
        "wheel_path": str(wheel_path),
        "install_script": str(install_script_path),
        "update_script": str(update_script_path),
        "notes_path": str(notes_path),
        "version": version,
    }
