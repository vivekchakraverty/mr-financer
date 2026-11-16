# Setup and operation

## Local server

Follow the [quick start](../README.md#run-locally). The web UI is at [localhost:8765](http://127.0.0.1:8765/) and the API reference is at [localhost:8765/docs](http://127.0.0.1:8765/docs).

The app is intended for a local or access-controlled installation. For network hosting, put it behind your identity and TLS boundary. Treat any imported contact data as available to users who can reach the app.

## Configuration

Copy `.env.example` to `.env` and configure the connectors you need. The **Data Sources** tab shows discovery and financial source availability. Restart the application after changing its configuration.

| Feature | Configuration |
| --- | --- |
| Competitor discovery | `MRF_SEARCH_PROVIDER`, `MRF_SEARCH_API_KEY`, optional Product Hunt and Foursquare settings |
| Financial registries | Optional Companies House, OpenDART and INPI credentials. Set `MRF_SEC_CONTACT` for SEC requests |
| Company reviews | Set the optional Trustpilot, Google Places and G2 settings when entitled to those APIs |
| Business Plan and pitch imagery | Set `MRF_BUSINESS_PLAN_SPACE_URL` and `MRF_PITCH_IMAGE_SPACE_URL` for your Spaces. Enter your Hugging Face token in the app when requested |
| Learn | Install yt-dlp on your PATH and enter a Hugging Face read token under **Learn → Connect quiz service**. Set `MRF_LEARN_SPACE_URL` to use another quiz service |
| Headless browser | The Windows launcher installs Chromium. For manual setup use `python -m playwright install chromium` |
| Browser egress | Set `MRF_LOAN_BROWSER_PROXY` or `MRF_INCORPORATION_BROWSER_PROXY` if your installation uses a source-specific proxy |

The app defaults to respecting publisher access rules. Treat empty live results as source gaps. Check the linked publisher page for current details.

## Load a private investor workbook

The Git checkout contains the feature and importer. Run the importer locally against your workbook, whose sector tabs use the original column format.

```powershell
New-Item -ItemType Directory -Force private-data | Out-Null
python scripts/import-investors.py "C:\path\to\investment-angel-one.xlsx" private-data/angel-investors.json.gz
```

In `.env` set:

```dotenv
MRF_INVESTOR_DIRECTORY=private-data/angel-investors.json.gz
```

Restart and open **Funding → Angel Investors**. When running the server from another directory, use an absolute path. Store the workbook and its output outside version control. The app accepts the previous application bundle's local dataset location when no explicit path is configured.

## Refresh incorporation research

The repository includes the compiled incorporation catalog. To rebuild it from a revised source workbook:

```bash
python scripts/import-incorporation.py "Business_Organization_Requirements_30_Economies.xlsx" src/mrfinancer/data/incorporation.json.gz
```

Restart after rebuilding. Check that country coverage, official URLs and workbook gaps still agree with the source document.

## Troubleshooting

- A provider marked unavailable may require credentials, entitlements, or permission for automated requests. Check its source card and provider documentation.
- If the browser cannot start, install Chromium using the same virtual environment as the server.
- If the investor directory could not be read, check that the configured file exists and rerun the importer.
- If a generation call fails, check the Space URL, Hugging Face token and quota.
