# docapi CLI Quickstart

This document explains how to install and run the current `docapi` CLI on another machine.

## Current Product Scope

The current CLI is a Python package that provides a `docapi` command.

Today it can:
- scan a backend package, path, or API route and list candidate endpoints
- let the operator choose one API
- generate `scan.json`, `analysis.json`, deterministic `api_config.json`, `quality_report.json`, and `manifest.json`
- export `api_spec.xlsx` and `export.json` from `docapi generate`
- keep provider mode optional with deterministic no-model fallback (`provider=none`)
- verify packaged runtime health with `docapi health`
- check for and apply wheel-based updates with `docapi self-update`
- generate release assets for maintainers with `docapi release`

Current limitations:
- no standalone `exe`
- no first-run setup wizard
- no automatic provider recovery when local model services are offline

## Prerequisites

- access to the backend source tree you want to scan
- optional access to the frontend source tree if you want frontend call evidence
- for the no-Python Windows installer, network access to the GitHub release assets
- for the manual wheel flow, Python 3.10 or newer

## Install

On Windows, the preferred install flow is the managed-runtime installer:

```powershell
irm https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.4/install-docapi.ps1 | iex
```

That installer does not require a preinstalled Python. It bootstraps `uv`, provisions a managed runtime, installs `docapi-tools`, and creates a user-local `docapi` command shim.

If you want the manual Python-based wheel flow instead:

```powershell
python -m pip install https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.4/docapi_tools-0.1.4-py3-none-any.whl
```

If you are working from source instead:

```powershell
cd C:\path\to\docapi-tools
python -m pip install -e .
```

If you run that command directly in the current PowerShell session, `docapi` should be available immediately after install.

If you instead launch the installer in a child PowerShell process or from a separate downloaded `.ps1` file, open a new terminal first. The managed-runtime installer updates the user `PATH` with `%USERPROFILE%\.docapi\bin`.

For the manual wheel flow, the user Scripts directory may not be on `PATH`.

On Windows PowerShell, you can add it for the current shell:

```powershell
$userBase = python -c "import site; print(site.USER_BASE)"
$userScripts = Join-Path $userBase "Scripts"
$env:Path = "$userScripts;$env:Path"
```

Then verify:

```powershell
docapi --version
docapi scan --help
docapi health
```

To check for a packaged update:

```powershell
docapi self-update --manifest https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.4/release-manifest.json --check
```

## How To Specify Scan Scope

The CLI uses explicit target modes. A user on any machine passes paths for their own environment.

### 1. Scan by package

Use this when you know the Java package name.

```powershell
docapi scan ^
  --package jp.co.fminc.socia.aplAprList ^
  --back-root D:\work\my-backend\src\main\java
```

Notes:
- `--back-root` is required for `--package`
- the package name is converted to a relative path under `--back-root`

### 2. Scan by path

Use this when you want the easiest cross-machine mode.

```powershell
docapi scan ^
  --path D:\work\my-backend\src\main\java\jp\co\fminc\socia\aplAprList
```

Notes:
- for `--path`, the CLI tries to infer the backend root from `src/main/java`
- this is usually the best option for another developer's machine because it avoids forcing them to know the exact Java source root separately

### 3. Scan by API route

Use this when you know the route already.

```powershell
docapi scan ^
  --api /api/aplAprList/show ^
  --back-root D:\work\my-backend\src\main\java
```

Notes:
- `--back-root` is required for `--api`
- the CLI searches controllers under that backend root

## Useful Options

- `--front-root <path>`: optional frontend source root for usage evidence
- `--output-dir <path>`: where run artifacts should be created
- `--pick <n>`: choose a candidate index non-interactively
- `--yes`: skip confirmation prompts
- `--non-interactive`: fail instead of prompting when user input is needed
- `--engine heuristic|gitnexus|hybrid`: analysis engine choice
- `--quality-gate off|report|strict`: quality control mode for generated `api_config.json`
- `--provider none|ollama`: local provider mode override
- `--provider-config <path>`: explicit provider config path

## Common Commands

### Scan only

```powershell
docapi scan --path D:\work\my-backend\src\main\java\jp\co\fminc\socia\aplAprList
```

### Analyze one selected API and write run artifacts

```powershell
docapi analyze ^
  --path D:\work\my-backend\src\main\java\jp\co\fminc\socia\aplAprList ^
  --pick 1 ^
  --yes ^
  --non-interactive ^
  --output-dir D:\tmp\docapi-out
```

### Generate the full end-to-end artifact set (including workbook)

```powershell
docapi generate ^
  --path D:\work\my-backend\src\main\java\jp\co\fminc\socia\aplAprList ^
  --pick 1 ^
  --yes ^
  --non-interactive ^
  --output-dir D:\tmp\docapi-out
```

## Output Artifacts

Each selected API gets its own run directory:

```text
YYYYMMDD-HHMMSS_<sanitized_api_id>/
  manifest.json
  scan.json
  analysis.json
  api_config.json
  quality_report.json
  api_spec.xlsx
  export.json
  audit.jsonl                      # only when provider is enabled
  llm/
    prompts/*.md                  # only when provider is enabled
    responses/*.json              # only when provider is enabled
    decisions/*.json              # only when provider is enabled
```

These artifacts are designed to be portable and auditable. They do not depend on your machine-specific paths except for the source roots that were used during the run and recorded in metadata.

## Cross-Machine Expectations

The CLI is portable across machines if the user provides their own source paths.

What changes between machines:
- the backend path
- the frontend path
- the output directory
- whether the user Scripts directory is on `PATH`

What should not change:
- CLI arguments
- artifact names
- candidate selection behavior
- baseline JSON contracts
- quality report schema
- workbook filename (`api_spec.xlsx`) when export succeeds

## Known Current Limitations

- No standalone installer or packaged `exe`
- No first-run setup wizard yet
- Provider-backed enhancement still depends on the user's local provider setup

Installability, GitHub-hosted wheel distribution, managed-runtime bootstrap, and self-update are now available through the current release workflow.
