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


DEFAULT_BOOTSTRAP_PYTHON = "3.13"
DEFAULT_BOOTSTRAP_UV_URL = "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip"


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
    wheel_name = str(manifest.get("wheel") or "").strip()
    if manifest_source and not _is_url(manifest_source) and wheel_name:
        local_candidate = Path(manifest_source).resolve().parent / wheel_name
        if local_candidate.exists():
            return str(local_candidate)

    explicit = str(manifest.get("install_spec") or "").strip()
    if explicit:
        return explicit

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
    template = """param(
[string]$ManifestUrl = "__MANIFEST_URL__",
[string]$Spec = "",
[string]$InstallRoot = "",
[string]$PythonVersion = "",
[switch]$SkipPathUpdate
)

$ErrorActionPreference = "Stop"
$upgrade = __UPGRADE_LITERAL__
$scriptDir = if ($MyInvocation.MyCommand.Path) { Split-Path -Parent $MyInvocation.MyCommand.Path } else { $PWD.Path }

function Get-Manifest {
  $manifestPath = Join-Path $scriptDir "__MANIFEST_FILENAME__"
  if (Test-Path $manifestPath) {
    return Get-Content $manifestPath -Raw | ConvertFrom-Json
  }
  if ($ManifestUrl) {
    return Invoke-RestMethod -UseBasicParsing $ManifestUrl
  }
  throw "No release manifest was provided."
}

function Resolve-InstallSpec($Manifest) {
  if ($Spec) {
    return $Spec
  }
  if ($Manifest.wheel) {
    $localWheel = Join-Path $scriptDir ([string]$Manifest.wheel)
    if (Test-Path $localWheel) {
      return $localWheel
    }
  }
  if ($Manifest.install_spec) {
    return [string]$Manifest.install_spec
  }
  if ($Manifest.wheel) {
    if ($ManifestUrl) {
      $manifestUri = [System.Uri]$ManifestUrl
      $baseUri = New-Object System.Uri($manifestUri, "./")
      return (New-Object System.Uri($baseUri, [string]$Manifest.wheel)).AbsoluteUri
    }
    return Join-Path $scriptDir ([string]$Manifest.wheel)
  }
  throw "Release manifest is missing install_spec/wheel."
}

function Get-ManagedPaths([string]$BaseDir) {
  return @{
    BaseDir = $BaseDir
    RuntimeDir = Join-Path $BaseDir "runtime"
    RuntimePython = Join-Path $BaseDir "runtime\\Scripts\\python.exe"
    RuntimeDocApi = Join-Path $BaseDir "runtime\\Scripts\\docapi.exe"
    BinDir = Join-Path $BaseDir "bin"
    CmdShim = Join-Path $BaseDir "bin\\docapi.cmd"
    PsShim = Join-Path $BaseDir "bin\\docapi.ps1"
    ToolsDir = Join-Path $BaseDir "tools"
    UvDir = Join-Path $BaseDir "tools\\uv"
    UvExe = Join-Path $BaseDir "tools\\uv\\uv.exe"
    DownloadDir = Join-Path $BaseDir "downloads"
    UvZip = Join-Path $BaseDir "downloads\\uv-windows.zip"
  }
}

function Get-UvExecutable([hashtable]$Paths, [string]$UvUrl) {
  if (Test-Path $Paths.UvExe) {
    return $Paths.UvExe
  }

  $systemUv = Get-Command uv -ErrorAction SilentlyContinue
  if ($systemUv) {
    return $systemUv.Source
  }

  New-Item -ItemType Directory -Force -Path $Paths.UvDir | Out-Null
  New-Item -ItemType Directory -Force -Path $Paths.DownloadDir | Out-Null
  Invoke-WebRequest -UseBasicParsing -Uri $UvUrl -OutFile $Paths.UvZip
  if (Test-Path $Paths.UvExe) {
    Remove-Item -Force $Paths.UvExe
  }
  Expand-Archive -LiteralPath $Paths.UvZip -DestinationPath $Paths.UvDir -Force
  if (-not (Test-Path $Paths.UvExe)) {
    throw "uv bootstrap failed: $($Paths.UvExe) was not created."
  }
  return $Paths.UvExe
}

function Ensure-RuntimePython([hashtable]$Paths, [string]$RequestedVersion, [string]$UvUrl) {
  if (Test-Path $Paths.RuntimePython) {
    return $Paths.RuntimePython
  }

  $uv = Get-UvExecutable -Paths $Paths -UvUrl $UvUrl
  if ($RequestedVersion) {
    & $uv python install $RequestedVersion
    if ($LASTEXITCODE -ne 0) {
      throw "uv python install failed."
    }
  }

  $uvArgs = @("venv", "--seed")
  if ($RequestedVersion) {
    $uvArgs += @("--python", $RequestedVersion)
  }
  $uvArgs += $Paths.RuntimeDir
  & $uv @uvArgs
  if ($LASTEXITCODE -ne 0) {
    throw "uv venv creation failed."
  }

  if (-not (Test-Path $Paths.RuntimePython)) {
    throw "Runtime Python was not created at $($Paths.RuntimePython)."
  }
  return $Paths.RuntimePython
}

function Install-DocApi([string]$PythonExe, [string]$InstallSpec, [bool]$DoUpgrade) {
  $resolvedInstallSpec = $InstallSpec
  if ($InstallSpec -match '^(https?)://') {
    $downloadName = [System.IO.Path]::GetFileName(([System.Uri]$InstallSpec).AbsolutePath)
    if (-not $downloadName) {
      $downloadName = "docapi-tools.whl"
    }
    New-Item -ItemType Directory -Force -Path $paths.DownloadDir | Out-Null
    $downloadPath = Join-Path $paths.DownloadDir $downloadName
    try {
      $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
      if ($curl) {
        & $curl.Source "-L" "--retry" "5" "--retry-all-errors" "--connect-timeout" "30" "-o" $downloadPath $InstallSpec
        if ($LASTEXITCODE -ne 0) {
          throw "curl download failed."
        }
      } else {
        Invoke-WebRequest -UseBasicParsing -Uri $InstallSpec -OutFile $downloadPath -TimeoutSec 600
      }
    } catch {
      if (Get-Command Start-BitsTransfer -ErrorAction SilentlyContinue) {
        Start-BitsTransfer -Source $InstallSpec -Destination $downloadPath
      } else {
        throw
      }
    }
    $resolvedInstallSpec = $downloadPath
  }

  $args = @("-m", "pip", "install")
  if ($DoUpgrade) {
    $args += "--upgrade"
  } else {
    $args += "--upgrade"
  }
  $args += $resolvedInstallSpec
  & $PythonExe @args
  if ($LASTEXITCODE -ne 0) {
    throw "docapi installation failed."
  }
}

function Write-CommandShims([hashtable]$Paths) {
  New-Item -ItemType Directory -Force -Path $Paths.BinDir | Out-Null

  $cmdShim = @"
@echo off
"%~dp0..\\runtime\\Scripts\\docapi.exe" %*
"@
  Set-Content -Path $Paths.CmdShim -Value $cmdShim -Encoding Ascii

  $psShim = @"
& "$PSScriptRoot\\..\\runtime\\Scripts\\docapi.exe" @args
"@
  Set-Content -Path $Paths.PsShim -Value $psShim -Encoding Utf8
}

function Ensure-UserPath([string]$BinDir, [bool]$ShouldSkip) {
  if ($ShouldSkip) {
    return $false
  }

  $currentUserPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $entries = @()
  if ($currentUserPath) {
    $entries = @($currentUserPath.Split(';') | Where-Object { $_ })
  }
  if ($entries -contains $BinDir) {
    $env:Path = "$BinDir;$env:Path"
    return $false
  }

  $updatedEntries = @($entries + $BinDir)
  $updatedPath = ($updatedEntries -join ';')
  [Environment]::SetEnvironmentVariable("Path", $updatedPath, "User")
  $env:Path = "$BinDir;$env:Path"
  return $true
}

$manifest = Get-Manifest
$resolvedSpec = Resolve-InstallSpec -Manifest $manifest
if (-not $InstallRoot) {
  $InstallRoot = Join-Path $HOME ".docapi"
}
if (-not $PythonVersion) {
  $PythonVersion = [string]$manifest.bootstrap.python_version
}
$uvUrl = [string]$manifest.bootstrap.uv_url
if (-not $uvUrl) {
  throw "Release manifest is missing bootstrap.uv_url."
}

$paths = Get-ManagedPaths -BaseDir $InstallRoot
New-Item -ItemType Directory -Force -Path $paths.BaseDir | Out-Null
$pythonExe = Ensure-RuntimePython -Paths $paths -RequestedVersion $PythonVersion -UvUrl $uvUrl
Install-DocApi -PythonExe $pythonExe -InstallSpec $resolvedSpec -DoUpgrade $upgrade
Write-CommandShims -Paths $paths
$pathChanged = Ensure-UserPath -BinDir $paths.BinDir -ShouldSkip $SkipPathUpdate

Write-Host ""
Write-Host "docapi install complete"
Write-Host "install_root: $InstallRoot"
Write-Host "runtime_python: $pythonExe"
Write-Host "command_shim: $($paths.CmdShim)"
if ($pathChanged) {
  Write-Host "user PATH updated: $($paths.BinDir)"
  Write-Host "Open a new terminal before using `docapi`."
} else {
  Write-Host "bin_dir: $($paths.BinDir)"
}
Write-Host ""
Write-Host "Quick check:"
Write-Host "  docapi --version"
"""
    return (
        template.replace("__MANIFEST_URL__", manifest_url)
        .replace("__UPGRADE_LITERAL__", upgrade_literal)
        .replace("__MANIFEST_FILENAME__", manifest_filename)
    )


def _render_release_notes(manifest: dict[str, Any]) -> str:
    install_hint = manifest.get("install_spec") or f".\\{manifest['wheel']}"
    update_hint = manifest.get("manifest_url") or ".\\release-manifest.json"
    script_hint = manifest.get("install_script_url") or ".\\install-docapi.ps1"
    return (
        f"docapi release {manifest['version']}\n\n"
        "Install examples:\n"
        f"- powershell -ExecutionPolicy Bypass -c \"irm {script_hint} | iex\"\n"
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
        "install_script_url": urljoin(normalized_base_url, "install-docapi.ps1") if normalized_base_url else None,
        "update_script_url": urljoin(normalized_base_url, "update-docapi.ps1") if normalized_base_url else None,
        "bootstrap": {
            "mode": "managed-venv",
            "python_version": DEFAULT_BOOTSTRAP_PYTHON,
            "uv_url": DEFAULT_BOOTSTRAP_UV_URL,
        },
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
