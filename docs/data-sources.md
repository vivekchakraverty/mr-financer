# Data sources and coverage

## How to read the results

Mr. Financer distinguishes direct matches from comparable products and links to the source of each check. Company accounts can require a registry key, filing access, or a confident legal-entity match. Paywalled filings and unresolved company names can leave gaps. Confirm the relevant period, currency, and units before using a figure in a business plan.

## Connectivity

| Area | Sources and constraints |
| --- | --- |
| Product discovery | Wikidata and enabled search adapters support general discovery. Product Hunt, Foursquare, search APIs, and opt-in scrapers depend on your configuration and provider access. |
| Published financial records | Connectors cover UK Companies House, US SEC filings, France INPI, South Korea OpenDART, Norway Brønnøysund, and Slovakia RegisterUZ where data is accessible. India MCA can supply manual lookup links. |
| Company reviews | Trustpilot, Google Places, and G2 depend on access to the corresponding API. Ratings and review samples can be incomplete. G2 usage is counted per authenticated caller and against the installation's shared quota. |
| Loan directories | Fintable and the country-specific source catalog display records as available. A directory listing is a starting point for researching a lender. |
| Angel investors | The app filters records from a locally imported workbook by sector. Source and contact details come from the workbook. |
| Incorporation | The compiled catalog covers 30 economies and links to official sources. The UI labels the workbook's scope, excerpt-only evidence, and gaps. Live requests can return partial or empty results when sources change or block access. |
| Document drafting | Forecasts use supplied assumptions. Optional narrative and image generation depend on the configured Hugging Face Spaces and the caller's token. |

Open **Data Sources** in the running app to see the discovery and finance connector status. Configuration and data import commands are in [Setup](setup.md).

