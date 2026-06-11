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
    raw_text: str | None = None


class LabelComplianceResult(BaseModel):
    is_compliant: bool
    issues: list[str]


class LabelReviewResponse(BaseModel):
    extraction: ExtractedLabelFields
    compliance: LabelComplianceResult


class BatchLabelReviewResponse(BaseModel):
    results: list[LabelReviewResponse]
    processed_count: int
    failed_count: int


class HealthResponse(BaseModel):
    status: str
