from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.release_tools import build_release_artifacts, compare_versions
from scripts.runtime_support import collect_health_report


PACKAGE_ROOT = Path(__file__).resolve().parents[1]


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


def test_collect_health_report_finds_packaged_assets() -> None:
    report = collect_health_report()

    assert report["status"] == "pass"
    assert any(check["name"] == "api_template" and check["status"] == "pass" for check in report["checks"])
    assert any(check["name"] == "project_config" and check["status"] == "pass" for check in report["checks"])


def test_docapi_health_json_reports_runtime_assets() -> None:
    result = _run_docapi("health", "--json")
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["tool_version"]
    assert payload["runtime_root"]


def test_docapi_self_update_check_reads_local_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "release-manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "name": "docapi-tools",
                "version": "9.9.9",
                "wheel": "docapi_tools-9.9.9-py3-none-any.whl",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    result = _run_docapi("self-update", "--manifest", str(manifest_path), "--check", "--json")
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["needs_update"] is True
    assert payload["target_version"] == "9.9.9"
    assert payload["install_spec"].endswith("docapi_tools-9.9.9-py3-none-any.whl")


def test_docapi_self_update_dry_run_with_explicit_spec() -> None:
    result = _run_docapi("self-update", "--spec", "docapi-tools==9.9.9", "--dry-run", "--json")
    assert result.returncode == 0, result.stderr

    payload = json.loads(result.stdout)
    assert payload["status"] == "dry-run"
    assert payload["install_spec"] == "docapi-tools==9.9.9"
    assert payload["command"][-1] == "docapi-tools==9.9.9"


def test_compare_versions_orders_semver_strings() -> None:
    assert compare_versions("0.1.0", "0.1.1") < 0
    assert compare_versions("0.1.1", "0.1.0") > 0
    assert compare_versions("0.1.0", "0.1.0") == 0


def test_build_release_artifacts_writes_manifest_and_scripts(monkeypatch, tmp_path: Path) -> None:
    def fake_run(command, check, capture_output, text):  # noqa: ANN001
        output_dir = Path(command[command.index("-w") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "docapi_tools-0.1.0-py3-none-any.whl").write_bytes(b"wheel-bytes")
        return subprocess.CompletedProcess(command, 0, "ok", "")

    monkeypatch.setattr("scripts.release_tools.subprocess.run", fake_run)

    report = build_release_artifacts(
        output_dir=tmp_path,
        project_root=PACKAGE_ROOT,
        base_url="https://example.com/docapi/v0.1.0",
    )

    manifest = json.loads(Path(report["manifest_path"]).read_text(encoding="utf-8"))
    install_script = Path(report["install_script"]).read_text(encoding="utf-8")
    update_script = Path(report["update_script"]).read_text(encoding="utf-8")

    assert report["status"] == "completed"
    assert manifest["version"] == "0.1.0"
    assert manifest["install_spec"] == "https://example.com/docapi/v0.1.0/docapi_tools-0.1.0-py3-none-any.whl"
    assert "release-manifest.json" in install_script
    assert "--upgrade" in update_script
