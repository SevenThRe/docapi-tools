# docapi CLI Distribution

`docapi-tools` can now be packaged as a wheel and distributed with install/update metadata for non-repo users.

## Maintainer Release Flow

From the source tree:

```powershell
python -m scripts.docapi_cli release --output-dir dist/release --base-url https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0
```

This produces:

- `docapi_tools-<version>-py3-none-any.whl`
- `release-manifest.json`
- `install-docapi.ps1`
- `update-docapi.ps1`
- `README.txt`

## User Install Flow

From a published wheel URL:

```powershell
python -m pip install https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0/docapi_tools-0.1.0-py3-none-any.whl
```

Then verify:

```powershell
docapi --version
docapi health
```

## User Hot Update Flow

If a release manifest is published:

```powershell
docapi self-update --manifest https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0/release-manifest.json
```

To only check whether an update exists:

```powershell
docapi self-update --manifest https://github.com/SevenThRe/docapi-tools/releases/download/v0.1.0/release-manifest.json --check
```

## User Config Preservation

To keep config outside the installed package, place overrides under `DOCAPI_HOME` or the default home directory:

- Windows default: `%USERPROFILE%\\.docapi\\project_config.json`
- Windows default: `%USERPROFILE%\\.docapi\\provider_config.json`

Updates replace the package install, but do not touch files stored in `DOCAPI_HOME`.
