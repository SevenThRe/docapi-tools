from __future__ import annotations

import json
from pathlib import Path

from scripts.docapi_cli import SelectionResult, run_provider_stage
from scripts.provider_audit import write_stage_artifacts
from scripts.providers.ollama_provider import OllamaProvider


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_ollama_provider_generate_text_parses_payload(monkeypatch) -> None:
    payload = {
        "response": "ok",
        "prompt_eval_count": 12,
        "eval_count": 34,
    }

    def fake_urlopen(req, timeout):  # noqa: ANN001
        assert timeout == 3
        return _FakeHttpResponse(payload)

    monkeypatch.setattr("scripts.providers.ollama_provider.request.urlopen", fake_urlopen)
    provider = OllamaProvider(base_url="http://127.0.0.1:11434", model="llama3.1", timeout_sec=3)
    result = provider.generate_text("hello")

    assert result["text"] == "ok"
    assert result["usage"]["prompt_eval_count"] == 12
    assert result["usage"]["eval_count"] == 34


def test_provider_audit_writes_prompt_response_and_decision(tmp_path: Path) -> None:
    write_stage_artifacts(
        tmp_path,
        stage="draft_enhancement",
        prompt="prompt text",
        response={"text": "result"},
        decision={"applied": False, "reason": "phase2"},
    )

    assert (tmp_path / "audit.jsonl").exists()
    assert (tmp_path / "llm" / "prompts" / "draft_enhancement.md").exists()
    assert (tmp_path / "llm" / "responses" / "draft_enhancement.json").exists()
    assert (tmp_path / "llm" / "decisions" / "draft_enhancement.json").exists()


def test_run_provider_stage_creates_local_artifacts_with_stubbed_ollama(monkeypatch, tmp_path: Path) -> None:
    def fake_generate_text(self, prompt: str):  # noqa: ANN001
        assert "High-priority context files" in prompt
        return {"text": "advice", "raw": {"response": "advice"}, "usage": None}

    monkeypatch.setattr("scripts.providers.ollama_provider.OllamaProvider.generate_text", fake_generate_text)
    selection = SelectionResult(
        candidate={
            "id": "api_aplaprlist_show",
            "method": "POST",
            "path": "/api/aplAprList/show",
            "summary": "show",
            "controller_class": "AplAprListController",
            "controller_method": "show",
            "confidence": "HIGH",
        },
        selected_index=1,
    )
    analysis_payload = {
        "analysis": {
            "llm_context_files": [
                {"type": "xml", "priority": 1, "path": "C:/work/AplAprListMapper.xml"},
            ]
        }
    }

    pipeline_steps, artifacts = run_provider_stage(
        run_dir=tmp_path,
        selection=selection,
        analysis_payload=analysis_payload,
        provider_settings={
            "name": "ollama",
            "config_path": None,
            "ollama": {
                "base_url": "http://127.0.0.1:11434",
                "model": "llama3.1",
                "timeout_sec": 30,
            },
        },
    )

    assert pipeline_steps[0]["status"] == "completed"
    assert artifacts is not None
    assert (tmp_path / artifacts["prompt"]).exists()
    assert (tmp_path / artifacts["response"]).exists()
    assert (tmp_path / artifacts["decision"]).exists()
    assert (tmp_path / "audit.jsonl").exists()
