from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

from scripts.runtime_support import resolve_user_config_path


DEFAULT_PROVIDER_CONFIG: dict[str, Any] = {
    "provider": "none",
    "ollama": {
        "base_url": "http://127.0.0.1:11434",
        "model": "llama3.1",
        "timeout_sec": 30,
    },
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _packaged_root() -> Path | None:
    try:
        return Path(str(resources.files("scripts").parent))
    except Exception:
        return None


def resolve_provider_config_path(path: str | None = None) -> Path | None:
    if path:
        candidate = Path(path).expanduser().resolve()
        if candidate.exists():
            return candidate
        raise ValueError(f"Provider config not found: {candidate}")

    user_candidate = resolve_user_config_path("provider_config.json")
    if user_candidate.exists():
        return user_candidate

    repo_candidate = _repo_root() / "configs" / "provider_config.json"
    if repo_candidate.exists():
        return repo_candidate

    packaged_root = _packaged_root()
    if packaged_root:
        packaged_candidate = packaged_root / "configs" / "provider_config.json"
        if packaged_candidate.exists():
            return packaged_candidate

    return None


def load_provider_config(path: str | None = None) -> dict[str, Any]:
    config_path = resolve_provider_config_path(path)
    loaded: dict[str, Any] = {}
    if config_path:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))

    provider = str(loaded.get("provider", DEFAULT_PROVIDER_CONFIG["provider"])).strip().lower()
    if provider not in {"none", "ollama"}:
        raise ValueError(f"Unsupported provider '{provider}'. Expected one of: none, ollama.")

    default_ollama = DEFAULT_PROVIDER_CONFIG["ollama"]
    loaded_ollama = loaded.get("ollama", {})

    return {
        "provider": provider,
        "ollama": {
            "base_url": str(loaded_ollama.get("base_url", default_ollama["base_url"])),
            "model": str(loaded_ollama.get("model", default_ollama["model"])),
            "timeout_sec": int(loaded_ollama.get("timeout_sec", default_ollama["timeout_sec"])),
        },
        "config_path": str(config_path) if config_path else None,
    }
