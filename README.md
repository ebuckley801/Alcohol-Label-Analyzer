# Treasury Take Home

**Alcohol Label Verifier** is a full-stack prototype that automates pre-market
review of alcohol beverage labels against U.S. TTB regulations. Upload a label
image (or a batch of up to ~300) and it uses **Azure AI Vision** OCR — with an
optional **Azure OpenAI** fallback for low-confidence captures — to extract the
brand name, class/type, alcohol content, net contents, origin, and government
health warning, then runs a **commodity-aware compliance engine**
(beer / wine / spirits) that flags issues with a severity
(`error` / `warning` / `info`) and the relevant **CFR citation**.

Checks include the mandated government warning (word-for-word, uppercase header,
relative type-size), wine sulfite declarations, responsible-party
(bottler/importer) presence, standard-of-fill, ABV plausibility and proof/ABV
consistency, recognized class/type designations, and cross-matching against
submitted application values.

**Stack:** FastAPI · Python 3.13 · React · TypeScript · Vite · Tailwind ·
Azure AI Vision · Azure OpenAI. Batch review runs client-side with bounded
concurrency; CSV export included.

## Structure
- `backend/`: FastAPI API + static hosting entrypoint for deployed frontend bundle
- `frontend/`: React + TypeScript + Vite client
- `api/` + `vercel.json`: Vercel serverless entrypoint for the prototype deployment
- `.github/workflows/`: CI (`ci.yml`) and Azure App Service deploy (`deploy.yml`) automation

## Backend Setup
1. `cd backend`
2. `python3.13 -m venv .venv`
3. `source .venv/bin/activate`
4. `pip install -r requirements-dev.txt`
5. `cp .env.example .env` and fill in required values
6. `uvicorn app.main:app --reload`

Backend API URLs:
- Health: `GET /api/health`
- Verify: `POST /api/v1/verify`
- Vision review: `POST /api/v1/review` (multipart form with image upload)

### Azure OpenAI Configuration (Backend)
The backend supports two vision providers using `VISION_PROVIDER`:
- `azure_ai_vision` (default, recommended)
- `azure_openai` (fallback)

For `azure_ai_vision`:
- `VISION_PROVIDER=azure_ai_vision`
- `AZURE_AI_VISION_ENDPOINT` (example: `https://<resource>.cognitiveservices.azure.com`)
- Optional: `AZURE_AI_VISION_KEY`
- Optional: `ENABLE_COUNTRY_AI_FALLBACK=true` (uses Azure OpenAI only when country is ambiguous)
- Optional: `ENABLE_OPENAI_CORE_FALLBACK=true` (uses Azure OpenAI when core fields are missing or OCR confidence is low)
- Optional: `OPENAI_CORE_FALLBACK_ON_MISSING_CORE_DATA=true`
- Optional: `OPENAI_CORE_FALLBACK_ON_LOW_CONFIDENCE=true`
- Optional: `OPENAI_CORE_FALLBACK_ON_SIZE_HEURISTIC=true` (uses Azure OpenAI when brand selection was size-heuristic-driven)
- Optional: `OPENAI_CORE_FALLBACK_ON_UNKNOWN_CLASS_TYPE=true` (uses Azure OpenAI when class/type is not in known alcohol type taxonomy)

When core fallback is enabled, Azure AI Vision remains primary and Azure OpenAI is called only when
fallback conditions are met. Missing fields are backfilled from fallback output, and low-confidence
captures can prefer fallback values for core fields.

If `AZURE_AI_VISION_KEY` is omitted, the app uses Managed Identity via `DefaultAzureCredential`.

For `azure_openai` fallback:
- `VISION_PROVIDER=azure_openai`
- `AZURE_OPENAI_ENDPOINT` (example: `https://<resource>.openai.azure.com`)
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT` (example: `gpt-4o`)
- `AZURE_OPENAI_API_VERSION` (default in code: `2024-10-21`)

Optional local OCR text dump (for debugging/testing only):
- `LOCAL_OCR_DUMP_ENABLED=true`
- `LOCAL_OCR_DUMP_PATH=local_debug/ocr_text.ndjson` (relative to `backend/` unless absolute)

When enabled, each `/api/v1/review` request appends one NDJSON record containing timestamp,
upload filename, content type, and extracted OCR raw text.

`/api/v1/review` accepts:
- `image` (required file, `image/*`)
- `expected_brand_name` (optional form field)
- `expected_alcohol_percentage` (optional form field)
- `expected_origin_country` (optional form field)

The endpoint returns extracted fields and a compliance result with issues.

## Frontend Setup
1. `cd frontend`
2. `npm install`
3. (optional) `cp .env.example .env`
4. `npm run dev`

By default, Vite proxies `/api` calls to `http://localhost:8000`.

## Local Quality Checks
- Backend:
  - `ruff check .`
  - `ruff format --check .`
  - `mypy .`
  - `pytest --cov=app --cov-report=term-missing`
- Frontend:
  - `npm run lint`
  - `npm run typecheck`
  - `npm run build`

## Makefile Shortcuts
- Install everything: `make install`
- Run full quality gate: `make check`
- Lint only: `make lint`
- Type checks only: `make typecheck`
- Backend tests: `make test`
- Frontend build: `make build`
- Start both dev servers from one terminal: `make dev`
- Start one side only: `make dev-backend` or `make dev-frontend`

## Deployment Notes

There are two supported deployment paths:

### 1. Vercel (current prototype host)
- `vercel.json` builds the frontend (`frontend/dist`) and exposes the FastAPI app
  through `api/index.py` for all `/api/*` routes.
- This is the fastest path for a public, demo-able prototype URL.
- **Caveat:** Vercel is *outside* the agency's Azure tenant. It is fine for a
  throwaway prototype that stores nothing sensitive, but is **not** a production
  target (see security note below).

### 2. Azure App Service (in-tenant)
The `deploy.yml` workflow builds the frontend, copies it into `backend/static/`,
and deploys the backend to Azure App Service with gunicorn + Uvicorn workers.
It expects these GitHub secrets:
- `AZURE_CREDENTIALS`
- `AZURE_WEBAPP_NAME`
- `AZURE_RESOURCE_GROUP`

### Security / tenant note
The vision pipeline uses **Azure AI Vision** and **Azure OpenAI**, both of which
run inside the Azure tenant's compliance boundary: prompts and images are not
sent to public OpenAI, are not used for model training, and stay in the selected
Azure region. For a real deployment this should be hosted in-tenant (Azure App
Service or Container Apps) with **Private Endpoints** so there is no outbound
traffic to public ML endpoints — directly addressing the firewall constraints
that broke the prior scanning-vendor pilot. Managed Identity
(`DefaultAzureCredential`) is preferred over API keys when running on Azure;
note that Managed Identity is **not** available on Vercel, so the prototype must
supply `AZURE_AI_VISION_KEY` there.

## Compliance Rules

Each review returns a structured list of issues. Every issue carries a
**severity** and, where applicable, a **CFR citation**, so reviewers can triage
hard rejections from judgment calls:

- `error` — a definite compliance failure; sets `is_compliant = false`.
- `warning` — likely a problem or something that needs a human look (e.g. an
  unrecognized standard of fill, an implausible ABV, a missing responsible-party
  statement). Does **not** by itself fail the label.
- `info` — contextual note (e.g. ABV omitted on a malt beverage, which is often
  allowed).

Checks are **commodity-aware** (beer / wine / spirits / unknown), derived from
the class/type designation:

- **Mandatory fields** — brand name, class/type, net contents on every label;
  ABV required on **wine and spirits** but treated as optional `info` on beer
  (27 CFR 4.32 / 5.63 / 7.65).
- **Government health warning** — present, header uppercase, mandated wording
  word-for-word, separateness, and a relative type-size heuristic
  (27 CFR 16.21–16.22).
- **Wine sulfite declaration** — `CONTAINS SULFITES` required on wine
  (27 CFR 4.32(e)).
- **Responsible party** — detects a bottler/producer/importer name-and-address
  statement (27 CFR 4.35 / 5.66 / 7.122); heuristic, so flagged as `warning`.
- **Standard of fill** — net contents validated against authorized sizes for
  spirits and wine (27 CFR 4.72 / 5.47); beer is unconstrained.
- **ABV plausibility** and **proof/ABV consistency** for spirits
  (proof must equal 2× ABV, 27 CFR 5.65(a)).
- **Recognized class/type** — designations outside the known vocabulary are
  flagged for review.
- **Cross-application matching** — brand, ABV (commodity-aware tolerance), and
  origin country compared against expected values from the form.

## Known Limitations

This is a prototype; the following are deliberate trade-offs to document rather
than fully solve within the scope.

### Government warning "too small" check is *relative*, not absolute
The size rule flags the warning when its average OCR text-box height is less
than **30% of the average text height across the whole label**
(`warning_average_height / average_line_height < 0.3`). It is a heuristic proxy,
not the literal TTB rule (which is an **absolute** minimum: ≥ 1 mm for
containers ≤ 237 mL, ≥ 2 mm above that). Consequences:

- **Misses uniformly tiny labels.** On a 50 mL miniature where *all* text is
  small, the ratio stays near 1.0 and the warning passes even if it violates the
  absolute 1 mm rule. The metric only detects a warning that is small *relative
  to other text on the same label*.
- **Misses labels with little large text.** A minimalist label that is mostly
  the warning gives a ratio near 1.0 and passes.
- **Can be skewed by line grouping.** Everything from the line containing
  "government warning" onward is treated as the warning block; an address, UPC,
  or normal-size line captured below it pulls the average up and can let a small
  warning through.
- **Box height ≠ font size.** All-caps vs. descenders, italics, condensed
  fonts, or rotated text change box height independently of legibility.
- **False positives are possible.** A perfectly legible warning on a label with
  very large brand art can dip under 0.3 and be flagged unnecessarily.
- **If print is so small OCR misreads the header**, `has_government_warning` is
  `False`, so the result is reported as *"warning missing"* rather than *"warning
  too small."*

A correct absolute check would require a physical scale reference (mm-per-pixel),
which a bare uploaded photo does not provide. That is left as future work.

### OCR extraction is heuristic and tuned to test labels
Brand / class-type selection uses geometry- and keyword-based scoring with some
hardcoded hints. It performs well on clear, front-facing labels but can misorder
fields on busy artwork, angled/glare photos, or unusual layouts. The optional
Azure OpenAI fallback backfills low-confidence captures but is off by default.

### Cross-application matching is field-limited
Brand, ABV, and origin country are checked against expected values supplied in
the form. A bottler/producer/importer name-and-address statement is detected
heuristically (keyword phrases such as "Bottled by") and flagged as a `warning`
when absent, but the address itself is not parsed or cross-verified, and
import-specific TTB rules for *how* the country-of-origin statement must appear
(placement, conspicuousness) are not evaluated — only the country value is
compared. This prototype does not integrate with COLA.

### ABV tolerance and standard-of-fill are simplified
Stated-ABV tolerances are commodity-aware with brackets (wine ±1.5% at/below
14% and ±1.0% above, malt ±0.3%, spirits ±0.15%), but real TTB tolerances have
finer per-product rules. The authorized standard-of-fill sets are a representative
subset; net contents in non-metric units (e.g. fluid ounces) are left for manual
review rather than validated.

### Performance / scale
Batch review runs client-side with bounded concurrency
(`MAX_CONCURRENT_REVIEWS = 6`). This keeps a 200–300 label batch responsive, but
throughput is ultimately bound by the Azure Vision service and any per-resource
rate limits; a production system would move batching server-side with a queue.
