# DocAPI Tools

`docapi-tools` is a Python CLI for scanning backend endpoints and generating deterministic Excel-based API design workbooks.

It is designed for teams that need auditable artifacts instead of ad-hoc AI output. A normal run can produce:

- `scan.json`
- `analysis.json`
- `api_config.json`
- `quality_report.json`
- `api_spec.xlsx`
- `export.json`

## Install

Install directly from the GitHub release wheel:

```powershell
python -m pip install https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0/docapi_tools-0.1.0-py3-none-any.whl
```

Then verify:

```powershell
docapi --version
docapi health
```

## Update

Check whether a newer release exists:

```powershell
docapi self-update --manifest https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0/release-manifest.json --check
```

Apply the update:

```powershell
docapi self-update --manifest https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0/release-manifest.json
```

## How Users Specify Scan Scope

Users always point the CLI at paths on their own machine. The tool does not depend on your local directory layout.

Scan by explicit path:

```powershell
docapi scan --path D:\work\backend\src\main\java\jp\co\fminc\socia\aplAprList
```

Scan by package:

```powershell
docapi scan --package jp.co.fminc.socia.aplAprList --back-root D:\work\backend\src\main\java
```

Scan by API route:

```powershell
docapi scan --api /api/aplAprList/show --back-root D:\work\backend\src\main\java
```

## Release Assets

Each packaged release can include:

- `docapi_tools-<version>-py3-none-any.whl`
- `release-manifest.json`
- `install-docapi.ps1`
- `update-docapi.ps1`

These assets make GitHub-hosted install and self-update possible without cloning the repo.

## Repository Contents

This public repo intentionally keeps user-facing docs and required skill material, while excluding local planning, harness, and AI workflow files from the release repository.
