from typing import Literal

from pydantic import BaseModel, Field


class LabelVerificationRequest(BaseModel):
    brand_name: str = Field(min_length=1, max_length=120)
    alcohol_percentage: float = Field(ge=0.0, le=100.0)
    has_government_warning: bool
    origin_country: str = Field(min_length=2, max_length=80)


class LabelVerificationResult(BaseModel):
    is_valid: bool
    confidence_score: float = Field(ge=0.0, le=1.0)
    issues: list[str]
    normalized_brand_name: str


class ExtractedLabelFields(BaseModel):
    brand_name: str
    class_type: str | None = None
    alcohol_percentage: float | None = Field(default=None, ge=0.0, le=100.0)
    net_contents: str | None = None
    origin_country: str | None = None
    has_government_warning: bool
    government_warning_text: str | None = None
    government_warning_is_all_uppercase: bool | None = None
    government_warning_font_size_ratio: float | None = Field(default=None, ge=0.0)
    raw_text: str | None = None
    ai_assisted_fields: list[str] = Field(default_factory=list)


IssueSeverity = Literal["error", "warning", "info"]


class ComplianceIssue(BaseModel):
    code: str
    message: str
    severity: IssueSeverity
    citation: str | None = None


class LabelComplianceResult(BaseModel):
    is_compliant: bool
    issues: list[str]
    issues_detail: list[ComplianceIssue] = Field(default_factory=list)


class LabelReviewResponse(BaseModel):
    extraction: ExtractedLabelFields
    compliance: LabelComplianceResult


class BatchLabelReviewResponse(BaseModel):
    results: list[LabelReviewResponse]
    processed_count: int
    failed_count: int


class HealthResponse(BaseModel):
    status: str
