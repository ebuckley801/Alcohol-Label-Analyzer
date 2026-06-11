import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.schemas import (
    HealthResponse,
    LabelReviewResponse,
    LabelVerificationRequest,
    LabelVerificationResult,
)
from app.services.compliance import ComplianceService
from app.services.verification import (
    LabelVerificationService,
    VerificationServiceError,
    build_label_verification_service,
)
from app.services.vision_extractor import (
    AzureOpenAICountryResolver,
    AzureOpenAIVisionExtractorService,
    AzureVisionReadExtractorService,
    HybridVisionExtractorService,
    VisionExtractor,
    VisionExtractorError,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Alcohol Label Verification API", version="0.1.0")
verification_service: LabelVerificationService = build_label_verification_service(
    fail_first_attempt=os.getenv("SIMULATE_TRANSIENT_FAILURE", "false").lower() == "true"
)
compliance_service = ComplianceService()

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _is_local_ocr_dump_enabled() -> bool:
    return os.getenv("LOCAL_OCR_DUMP_ENABLED", "false").strip().lower() == "true"


def _resolve_local_ocr_dump_path() -> Path:
    configured = os.getenv("LOCAL_OCR_DUMP_PATH", "local_debug/ocr_text.ndjson").strip()
    path = Path(configured)
    if path.is_absolute():
        return path
    return BACKEND_DIR / path


def _write_local_ocr_dump(
    *,
    upload_filename: str | None,
    content_type: str,
    raw_text: str | None,
) -> None:
    if not _is_local_ocr_dump_enabled():
        return

    try:
        output_path = _resolve_local_ocr_dump_path()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "upload_filename": upload_filename,
            "content_type": content_type,
            "raw_text": raw_text,
        }
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except OSError:
        logger.exception("local_ocr_dump_write_failed")


def _build_vision_service() -> VisionExtractor:
    provider = os.getenv("VISION_PROVIDER", "azure_ai_vision").strip().lower()
    if provider == "azure_ai_vision":
        endpoint = os.getenv("AZURE_AI_VISION_ENDPOINT", "")
        api_key = os.getenv("AZURE_AI_VISION_KEY")
        if not endpoint:
            raise VisionExtractorError("Azure AI Vision endpoint missing")

        country_resolver: AzureOpenAICountryResolver | None = None
        if os.getenv("ENABLE_COUNTRY_AI_FALLBACK", "false").lower() == "true":
            openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
            openai_key = os.getenv("AZURE_OPENAI_API_KEY", "")
            openai_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
            openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
            if openai_endpoint and openai_key:
                country_resolver = AzureOpenAICountryResolver(
                    endpoint=openai_endpoint,
                    api_key=openai_key,
                    deployment=openai_deployment,
                    api_version=openai_api_version,
                )

        primary_service = AzureVisionReadExtractorService(
            endpoint=endpoint,
            api_key=api_key,
            country_resolver=country_resolver,
        )

        openai_core_fallback_enabled = (
            os.getenv("ENABLE_OPENAI_CORE_FALLBACK", "false").lower() == "true"
        )
        if not openai_core_fallback_enabled:
            return primary_service

        openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        openai_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        openai_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")
        if not openai_endpoint or not openai_key:
            logger.warning("openai_core_fallback_disabled_missing_configuration")
            return primary_service

        fallback_service = AzureOpenAIVisionExtractorService(
            endpoint=openai_endpoint,
            api_key=openai_key,
            deployment=openai_deployment,
            api_version=openai_api_version,
        )

        return HybridVisionExtractorService(
            primary=primary_service,
            fallback=fallback_service,
            fallback_on_missing_core_data=(
                os.getenv("OPENAI_CORE_FALLBACK_ON_MISSING_CORE_DATA", "true").lower() == "true"
            ),
            fallback_on_low_quality=(
                os.getenv("OPENAI_CORE_FALLBACK_ON_LOW_CONFIDENCE", "true").lower() == "true"
            ),
            fallback_on_size_heuristic_selection=(
                os.getenv("OPENAI_CORE_FALLBACK_ON_SIZE_HEURISTIC", "true").lower() == "true"
            ),
            fallback_on_unknown_class_type=(
                os.getenv("OPENAI_CORE_FALLBACK_ON_UNKNOWN_CLASS_TYPE", "true").lower() == "true"
            ),
        )

    if provider == "azure_openai":
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        api_key = os.getenv("AZURE_OPENAI_API_KEY", "")
        deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21")

        if not endpoint or not api_key:
            raise VisionExtractorError("Azure OpenAI configuration missing")

        return AzureOpenAIVisionExtractorService(
            endpoint=endpoint,
            api_key=api_key,
            deployment=deployment,
            api_version=api_version,
        )

    raise VisionExtractorError("Unsupported vision provider configured")


STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.post("/api/v1/verify", response_model=LabelVerificationResult)
def verify_label(payload: LabelVerificationRequest) -> LabelVerificationResult:
    try:
        return verification_service.verify_label(payload)
    except VerificationServiceError as exc:
        logger.exception("label_verification_failed_after_retries")
        raise HTTPException(status_code=503, detail="Verification service unavailable") from exc


@app.post("/api/v1/review", response_model=LabelReviewResponse)
async def review_label(
    image: Annotated[UploadFile, File(description="Label image")],
    expected_brand_name: Annotated[str | None, Form()] = None,
    expected_alcohol_percentage: Annotated[float | None, Form()] = None,
    expected_origin_country: Annotated[str | None, Form()] = None,
) -> LabelReviewResponse:
    if image.content_type is None or not image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file must be an image")

    file_bytes = await image.read()
    if not file_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        vision_service = _build_vision_service()
        extraction = vision_service.extract_from_image(file_bytes, image.content_type)
        _write_local_ocr_dump(
            upload_filename=image.filename,
            content_type=image.content_type,
            raw_text=extraction.raw_text,
        )
        compliance = compliance_service.evaluate(
            extraction,
            expected_brand_name=expected_brand_name,
            expected_alcohol_percentage=expected_alcohol_percentage,
            expected_origin_country=expected_origin_country,
        )
    except VisionExtractorError as exc:
        logger.exception("label_review_failed", extra={"upload_filename": image.filename})
        raise HTTPException(
            status_code=503,
            detail="Vision extraction service unavailable",
        ) from exc

    return LabelReviewResponse(extraction=extraction, compliance=compliance)


@app.get("/{path_name:path}")
def serve_spa(path_name: str) -> FileResponse:
    if not STATIC_DIR.exists():
        raise HTTPException(status_code=404, detail="Frontend not deployed")

    target_file = STATIC_DIR / path_name
    if path_name and target_file.exists() and target_file.is_file():
        return FileResponse(target_file)

    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="Frontend entrypoint missing")
    return FileResponse(index_file)
