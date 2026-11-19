# Mr. Financer

<img src="src/mrfinancer/web/app-icon.png" width="96" height="96" alt="An older man holding a bag of cash">

Mr. Financer is a local-first research workspace for founders. Compare similar products, review company filings, draft a plan and pitch outline, and explore funding and incorporation sources.

| Workspace | What you can do |
| --- | --- |
| Ideation | Compare products and services, inspect evidence, and review company financials and reviews |
| Documents | Draft a business plan or pitch outline from a questionnaire, export plans to PDF or Word, and turn batches of images into PowerPoint decks |
| Funding | Search Fintable, browse lender directories by country, and query your locally imported investor workbook |
| Incorporation | Review workbook findings and live official pages for 30 economies |
| Learn | Watch a 24-topic founder course, generate quizzes from captions, and track your results locally |
| Data Sources | See which discovery and finance connectors are configured |

## Run locally

Download the executable for your system from the [latest release](https://github.com/vivekchakraverty/mr-financer/releases/latest). On Windows, run the `.exe`. On macOS and Linux, extract the archive, open a terminal in that folder, and run `./mrfinancer`. The app opens at [localhost:8765](http://127.0.0.1:8765/).

### Run from source

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

## Ideation viability brief

After finding matches, open **Could this idea pay?** Enter actual or estimated
sales and costs, keeping unavailable figures as Unknown. **Calculate and retrieve
evidence** computes monthly scenarios, contribution and operating margins,
break-even and profit-target volumes, startup funding gaps, and simple cash
runway and payback indicators. Owner's pay is separate from other running costs.
These single-offer scenarios assume immediate payment; the Business Plan supports
a fuller monthly cash forecast.

**Create evidence-based brief** sends those calculated figures, relevant retrieved
search records, reviewed company research and filings, and a sourced business
guidance corpus to the selected model. The brief states the geographic target,
labels other-market and unlocated evidence, and links citations to original
sources, dated input records and calculation methods. Missing data stays visible.
Guidance comes from the US SBA and describes general business methods; it is not
local legal advice. Financials and company research enter the brief after you
fetch them in the existing Ideation controls. Search snapshots expire after an hour.

Brief generation defaults to **Qwen3.5-9B on ZeroGPU**, using the existing
[protected model service](https://huggingface.co/spaces/vivekchakraverty/mr-financer-business-plan).
Enter your personal Hugging Face token. Submission and result retrieval both
forward your token, and the ZeroGPU scheduler applies your account's quota and
eligible credits. The service's source and model mount remain protected.

The optional CPU mode uses Qwen3-4B-Instruct-2507 Q4_K_M on the deployed
[Ideation CPU Space](https://huggingface.co/spaces/vivekchakraverty/mr-financer-ideation)
as a fallback. Set `MRF_VIABILITY_CPU_URL` to override its root URL. The Docker service
source is under [hf-ideation-space](hf-ideation-space). For local generation, set
`MRF_VIABILITY_CPU_URL=http://127.0.0.1:8080` and start a local llama.cpp server with
`--n-gpu-layers 0 --ctx-size 24576 --parallel 1 --chat-template chatml --skip-chat-parsing`. Local generation needs no token; hosted
generation requires the requesting user's HF personal access token.

The ZeroGPU option calls `/draft_viability` in the backend under [hf-space](hf-space).
It shares the loaded model and queue with business plans. Tokenizer indexing runs
before a GPU is allocated, and generation uses a constrained citation schema.
CPU generation may take several minutes; the calculation preview needs no model.

## Learn

Open **Learn**, select a topic, and choose **Find videos** to shortlist three
YouTube lessons. Install [yt-dlp](https://github.com/yt-dlp/yt-dlp#installation)
on your PATH. You can also choose a replacement video by URL. A lesson needs
usable English captions. Search ranks candidates by transcript fit, feedback in
sampled comments when available, and likes per view in context.

**Generate quiz** asks the CPU service for ten fresh questions from the selected
video's transcript: six multiple-choice, three fill-in-the-blank with answer
choices, and one three-pair matching question. The app takes correct phrases
from the captions and asks the model to frame the questions around them. No
typed answers are required. Enter a Hugging Face read token from your account
under **Learn → Connect quiz service**, or set `MRF_LEARN_SPACE_TOKEN` in the server environment. The hosted quiz service validates the token with
Hugging Face. The app hides the key until submission, records each score, and links explanations
to the video timestamps. Quiz history and selections are stored in
`.mrfinancer-learn.sqlite` by default.

To use a different CPU Space, deploy the files under [hf-learn-space](hf-learn-space)
to a Docker Space and set `MRF_LEARN_SPACE_URL`. Set
`MRF_LEARN_COLLECT_COMMENTS=false` to rank by transcript fit and engagement
without comment classification.

## Documentation

- [Installation, configuration, and data imports](docs/setup.md)
- [Architecture and request flow](docs/architecture.md)
- [Data sources and coverage notes](docs/data-sources.md)
- [Contributing and tests](CONTRIBUTING.md)

## Contributors

- **Vivek Chakraverty** · Project creator and maintainer.
- **Codex** · AI-assisted implementation, testing, and documentation.

See [CONTRIBUTORS.md](CONTRIBUTORS.md) for attribution.
