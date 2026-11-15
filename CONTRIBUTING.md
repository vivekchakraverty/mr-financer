# Contributing to Mr. Financer

## Make and verify a change

Use Python 3.11 or later and follow [local setup](docs/setup.md). Install development dependencies and run the test suite from the repository root.

```bash
python -m pip install -e ".[dev]"
python -m pytest -q
```

Submit focused changes with a description of the user-facing behavior and the checks you ran. For connectors, include the source and its expected response format. Use synthetic or appropriately shareable fixtures in tests. For browser UI changes, check a narrow mobile viewport as well as desktop.

## Source and data handling

Keep provider credentials in `.env` or your deployment's secret store. Keep investor contacts and generated investor exports in `private-data/`. When changing extraction or financial calculations, preserve references to the original publisher and the distinction between verified data and estimates.

Report possible credential exposure privately to the maintainer through GitHub's security reporting option, if available. Include the affected feature and reproduction steps with credentials and personal information removed.
