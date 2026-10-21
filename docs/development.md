# Development and validation

## Working principles

Keep code changes separate from documentation-only work. PR CI is the intended validation gate; deployment into field equipment requires a distinct authorized operation. Do not assume a successful workflow dispatch means the new version is installed and healthy.

## Development and tests

Install development dependencies with:

```bash
python -m pip install -e ".[dev]"
```

The main test boundaries are:

```bash
python -m pytest tests/ansible
python -m pytest tests --ignore=tests/ocpp_discover --ignore=tests/ansible
python -m pytest tests/ocpp_discover
```

CI runs those suites on:

```text
Debian 12 / Python 3.11 / ARM64   primary appliance target
Debian 13 / Python 3.13 / ARM64   compatibility target
```

Tests cover current behavior, request validation, safety constraints, and machine-readable output contracts.

## Source layout

```text
src/
  ocpp_csms/          OCPP server, evidence, transactions and control
  ocpp_discover/      resident discovery, adaptation and reconciliation

ansible/              canonical appliance convergence
deploy.sh              canonical deployment wrapper
```

See [README](../README.md) for entry points and the files in `docs/` for operational contracts.




## Validation for documentation changes

- Check every documented command against the current parser and `--help` before asserting exact flags/defaults.
- Check paths, systemd names, and schema constants against the source and deployment playbooks.
- Render Markdown to catch escaped fences and broken links.
- Keep environment-specific credentials, hostnames, and private operational details out of the repo.
