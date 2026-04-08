from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.provider_config import load_provider_config


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "phase1"
BACK_ROOT = FIXTURE_ROOT / "back" / "src" / "main" / "java"
CONTROLLER_DIR = BACK_ROOT / "jp" / "co" / "fminc" / "socia" / "aplAprList"


def _cli_command() -> list[str]:
    return [sys.executable, "-m", "scripts.docapi_cli"]


def _run_docapi(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*_cli_command(), *args],
        cwd=PACKAGE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_load_provider_config_defaults_to_none() -> None:
    config = load_provider_config(None)
    assert config["provider"] == "none"
    assert "ollama" in config
    assert config["ollama"]["base_url"].startswith("http://")


def test_load_provider_config_prefers_docapi_home_override(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "provider_config.json"
    config_path.write_text(
        json.dumps(
            {
                "provider": "ollama",
                "ollama": {
                    "base_url": "http://localhost:22434",
                    "model": "mistral",
                    "timeout_sec": 45,
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCAPI_HOME", str(tmp_path))

    config = load_provider_config(None)

    assert config["provider"] == "ollama"
    assert config["ollama"]["base_url"] == "http://localhost:22434"


def test_docapi_generate_manifest_contains_provider_defaults(tmp_path: Path) -> None:
    result = _run_docapi(
        "generate",
        "--path",
        str(CONTROLLER_DIR),
        "--pick",
        "1",
        "--yes",
        "--non-interactive",
        "--output-dir",
        str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
    run_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"]["name"] == "none"
    assert manifest["pipeline"]


def test_docapi_draft_accepts_provider_override_without_network_call(tmp_path: Path) -> None:
    result = _run_docapi(
        "draft",
        "--path",
        str(CONTROLLER_DIR),
        "--pick",
        "1",
        "--yes",
        "--non-interactive",
        "--provider",
        "ollama",
        "--output-dir",
        str(tmp_path),
    )

    assert result.returncode == 0, result.stderr
