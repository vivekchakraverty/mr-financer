# Mr. Financer

<img src="src/mrfinancer/web/app-icon.png" width="96" height="96" alt="An older man holding a bag of cash">

Mr. Financer is a local-first research workspace for founders. Compare similar products, review company filings, draft a plan and pitch outline, and explore funding and incorporation sources.

| Workspace | What you can do |
| --- | --- |
| Ideation | Compare products and services, inspect evidence, and review company financials and reviews |
| Documents | Draft a business plan or pitch outline from a questionnaire, export plans to PDF or Word, and turn batches of images into PowerPoint decks |
| Funding | Search Fintable, browse lender directories by country, and query your locally imported investor workbook |
| Incorporation | Review workbook findings and live official pages for 30 economies |
| Data Sources | See which discovery and finance connectors are configured |

## Run locally

Use Python 3.11 or later. On Windows, run this from the repository root:

```powershell
powershell -ExecutionPolicy RemoteSigned -File .\Start-MrFinancer.ps1
```

The launcher creates a virtual environment, installs the app and Chromium, and starts Mr. Financer at [localhost:8765](http://127.0.0.1:8765/).

On macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m playwright install chromium
cp .env.example .env
python -m uvicorn mrfinancer.api:app --host 127.0.0.1 --port 8765
```

On Linux, use `python -m playwright install --with-deps chromium` if Chromium's system libraries are missing. Core research, forecast previews and workbook browsing can run before you configure optional providers.

## Documentation

- [Installation, configuration, and data imports](docs/setup.md)
- [Architecture and request flow](docs/architecture.md)
- [Data sources and coverage notes](docs/data-sources.md)
- [Contributing and tests](CONTRIBUTING.md)

## Contributors

- **Vivek Chakraverty** · Project creator and maintainer.
- **Codex** · AI-assisted implementation, testing, and documentation.

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for attribution.
