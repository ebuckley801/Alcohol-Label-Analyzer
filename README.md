# Treasury Take Home

Full-stack scaffold for an Azure-deployed alcohol label verification system.

## Structure
- `backend/`: FastAPI API + static hosting entrypoint for deployed frontend bundle
- `frontend/`: React + TypeScript + Vite client
- `.github/workflows/`: CI and deploy automation
- `ENGINEERING_RULES.md`: repository quality and implementation standards

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
The existing deploy workflow expects these GitHub secrets:
- `AZURE_CREDENTIALS`
- `AZURE_WEBAPP_NAME`
- `AZURE_RESOURCE_GROUP`

On deploy, frontend artifacts are copied into `backend/static/`, and App Service starts the backend with gunicorn + Uvicorn workers.
