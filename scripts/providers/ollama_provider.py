from __future__ import annotations

import json
from typing import Any
from urllib import error, request


class OllamaProvider:
    def __init__(self, *, base_url: str, model: str, timeout_sec: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec

    def generate_text(self, prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
        }
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw_body = resp.read().decode("utf-8")
        except error.URLError as exc:
            raise RuntimeError(f"unable to reach Ollama at {self.base_url}: {exc}") from exc
        except TimeoutError as exc:
            raise RuntimeError(f"Ollama request timed out after {self.timeout_sec}s") from exc

        try:
            payload_json = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Ollama returned non-JSON response") from exc

        usage = None
        if "prompt_eval_count" in payload_json or "eval_count" in payload_json:
            usage = {
                "prompt_eval_count": payload_json.get("prompt_eval_count"),
                "eval_count": payload_json.get("eval_count"),
            }

        return {
            "text": payload_json.get("response", ""),
            "raw": payload_json,
            "usage": usage,
        }
