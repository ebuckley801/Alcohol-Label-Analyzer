import logging
from collections.abc import Callable

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.schemas import LabelVerificationRequest, LabelVerificationResult

logger = logging.getLogger(__name__)


class VerificationServiceError(RuntimeError):
    """Raised when verification logic cannot complete after retries."""


class LabelVerificationService:
    def __init__(self, fail_first_attempt: bool = False) -> None:
        self._fail_first_attempt = fail_first_attempt
        self._attempt_count = 0

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.1, min=0.1, max=0.4),
        retry=retry_if_exception_type(VerificationServiceError),
        reraise=True,
    )
    def verify_label(self, payload: LabelVerificationRequest) -> LabelVerificationResult:
        self._attempt_count += 1
        logger.info("label_verification_started", extra={"attempt": self._attempt_count})

        # This hook enables testing and simulates transient infrastructure failures.
        if self._fail_first_attempt and self._attempt_count == 1:
            logger.warning("label_verification_transient_failure")
            raise VerificationServiceError("Transient verification failure")

        issues = self._validate_fields(payload)
        confidence_score = self._score_confidence(payload, issues)

        result = LabelVerificationResult(
            is_valid=not issues,
            confidence_score=confidence_score,
            issues=issues,
            normalized_brand_name=payload.brand_name.strip().title(),
        )
        logger.info(
            "label_verification_completed",
            extra={"is_valid": result.is_valid, "issue_count": len(result.issues)},
        )
        return result

    def _validate_fields(self, payload: LabelVerificationRequest) -> list[str]:
        rules: list[tuple[bool, str]] = [
            (payload.has_government_warning, "Government warning is required."),
            (payload.alcohol_percentage > 0, "Alcohol percentage must be greater than 0."),
            (len(payload.brand_name.strip()) >= 2, "Brand name must be at least 2 characters."),
            (
                len(payload.origin_country.strip()) >= 2,
                "Origin country must be at least 2 characters.",
            ),
        ]

        return [message for condition, message in rules if not condition]

    def _score_confidence(self, payload: LabelVerificationRequest, issues: list[str]) -> float:
        base_score = 0.95
        issue_penalty = min(len(issues) * 0.2, 0.8)
        origin_bonus = 0.03 if len(payload.origin_country.strip()) > 3 else 0.0
        return max(0.0, min(1.0, base_score - issue_penalty + origin_bonus))


def build_label_verification_service(
    fail_first_attempt: bool = False,
    factory: Callable[[bool], LabelVerificationService] | None = None,
) -> LabelVerificationService:
    service_factory = factory or LabelVerificationService
    return service_factory(fail_first_attempt)
