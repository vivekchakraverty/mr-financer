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
| `viability.py`, `viability_financials.py`, `viability_retrieval.py`, `viability_contract.py` | Ideation calculators, retrieval, citation validation and model requests |
| `hf-ideation-space/` | Qwen3.5-4B CPU brief service; the existing ZeroGPU service also supports briefs |
| `tests/` | API, calculation, extraction and provider-contract checks |

## How results flow

1. The browser sends an idea, jurisdiction, or topic to the local API.
2. The API uses packaged catalogs, an optional locally imported investor dataset, or provider adapters.
3. Live directory checks use the source URLs in the catalog. Supported search portals run in the shared headless browser.
4. Results return to the browser with their context and source links. Live incorporation excerpts are displayed separately from workbook findings.
5. Document workflows compute projections in Python. Separate Hugging Face Spaces may draft narrative text or generate optional slide illustrations.

## Viability brief

Discovery creates a bounded, hour-long server-side evidence snapshot. Company
research and financial review append records to that search. The browser supplies
only the snapshot ID, financial assumptions and deployment mode to the brief API.
Amounts use Decimal arithmetic, explicit unknowns, same-period/scope/currency
comparisons and ceiling-rounded break-even volumes. The model never supplies the
published financial tables.

Retrieval splits already fetched records into source-linked chunks and ranks them
by inverse document frequency with category quotas for filings, prices, reviews,
demand signals and listings. A reviewed SBA guidance corpus is included as method
context, separately labeled from market evidence. No new web or paid G2 fetch is
triggered by brief generation. Curated guidance is dated and needs periodic review.

The bounded packet includes geographic scope, coverage, calculated scenarios,
inputs and retrieved citations. CPU and ZeroGPU services share a JSON contract.
Generated prose must cite packet IDs and pass number/link checks before display;
this checks format and reference availability, not semantic entailment. The UI
renders plain text, source links, calculation methods and geographic limitations.
Changing inputs, changing search or fetching further evidence invalidates the brief.
