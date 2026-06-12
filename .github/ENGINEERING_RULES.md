# Engineering Rules

## 1. Repository Structure
- Keep backend API code in `backend/app/`.
- Keep backend tests in `backend/tests/`.
- Keep frontend source code in `frontend/src/`.
- Keep CI and deployment workflow updates in `.github/workflows/`.
- Prefer small, single-purpose modules and avoid deep folder nesting.

## 2. Documentation Standards
- Every new endpoint must include request/response model documentation in code via schema types.
- Public modules should start with a short docstring explaining intent.
- Pull requests must describe behavior changes, test evidence, and rollback considerations.
- Update `README.md` when adding setup steps, env vars, scripts, or architecture changes.

## 3. Commenting Standards
- Add comments only when logic is non-obvious or there are important tradeoffs.
- Do not add comments that repeat the code literally.
- Keep comments current; stale comments must be removed in the same change.

## 4. Error Handling Standards
- Handle known operational errors close to the boundary (HTTP, external calls, file I/O).
- Return user-safe API error messages; log full technical details server-side.
- Do not swallow exceptions silently.
- Map internal exceptions to stable HTTP status codes and response formats.

## 5. Retry Logic Standards
- Use bounded retries with exponential backoff for transient failures only.
- Never retry validation or deterministic business rule failures.
- Emit log entries on each retry attempt with operation name and attempt count.
- Ensure retry settings (attempts, delay, timeout) are explicit and reviewable.

## 6. Logging and Debugging Standards
- Use structured logs with event names (for example: `label_verification_started`).
- Log at appropriate levels: `INFO` for lifecycle events, `WARNING` for recoverable issues, `ERROR`/`EXCEPTION` for failures.
- Include traceable context values (request IDs, attempt counts, key identifiers) when available.
- Never log secrets, credentials, or raw PII.

## 7. Testing and Quality Gates
- Backend changes must pass: `ruff check .`, `ruff format --check .`, `mypy .`, `pytest --cov=app`.
- Frontend changes must pass: `npm run lint`, `npm run typecheck`, `npm run build`.
- Add or update tests for behavior changes before merging.

## 8. File Structure and Naming
- Use clear, descriptive, lowercase file names with underscores for Python and camelCase for frontend utility modules.
- Keep related types, logic, and tests close together by feature.
- Avoid catch-all files such as `utils.py` or `helpers.ts` without strong boundaries.
