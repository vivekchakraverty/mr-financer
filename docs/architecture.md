# Architecture

The application serves a browser workspace, a FastAPI API, and optional CLI commands from the same Python package.

| Location | Responsibility |
| --- | --- |
| `src/mrfinancer/web/` | Browser views, navigation, styles, application icon, and bundled questionnaires |
| `src/mrfinancer/api.py` and `workspace.py` | HTTP entry points, UI assets, project discovery, comparisons, and review |
| `src/mrfinancer/adapters/` | Provider-specific discovery and registry requests |
| `loans.py`, `loan_browser.py`, and `incorporation.py` | Public sources, workbook references, and headless browser searches |
| `investors.py` and `scripts/import-investors.py` | Locally loaded investor data and sector filtering |
| `business_plan.py`, `plan_financials.py`, `pitch_deck.py`, and `pitch_art.py` | Plan workflows, financial projections, pitch outlines, and optional image requests |
| `hf-space/` | Source for the separate business-plan backend |
| `tests/` | API, calculation, extraction and provider-contract checks |

## How results flow

1. The browser sends an idea, jurisdiction, or topic to the local API.
2. The API uses packaged catalogs, an optional locally imported investor dataset, or provider adapters.
3. Live directory checks use the source URLs in the catalog. Supported search portals run in the shared headless browser.
4. Results return to the browser with their context and source links. Live incorporation excerpts are displayed separately from workbook findings.
5. Document workflows compute projections in Python. Separate Hugging Face Spaces may draft narrative text or generate optional slide illustrations.
