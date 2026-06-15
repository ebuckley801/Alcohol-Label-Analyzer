from fastapi.testclient import TestClient

from app.main import app
from app.schemas import ExtractedLabelFields
from app.services.vision_extractor import VisionExtractorError

client = TestClient(app)


def test_health_returns_ok() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_verify_label_valid_payload() -> None:
    payload = {
        "brand_name": "Blue Harbor",
        "alcohol_percentage": 12.5,
        "has_government_warning": True,
        "origin_country": "France",
    }

    response = client.post("/api/v1/verify", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is True
    assert body["issues"] == []
    assert 0.0 <= body["confidence_score"] <= 1.0


def test_verify_label_invalid_payload() -> None:
    payload = {
        "brand_name": "A",
        "alcohol_percentage": 0,
        "has_government_warning": False,
        "origin_country": "US",
    }

    response = client.post("/api/v1/verify", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["is_valid"] is False
    assert len(body["issues"]) >= 2


def test_frontend_served_when_built() -> None:
    response = client.get("/")

    # The built frontend is copied into backend/static during the build, so the
    # root path serves index.html. When static/ is absent, the handler instead
    # returns 404 "Frontend not deployed".
    if response.status_code == 200:
        assert "text/html" in response.headers["content-type"]
    else:
        assert response.status_code == 404
        assert response.json()["detail"] == "Frontend not deployed"


def test_review_endpoint_success(monkeypatch) -> None:
    class DummyVisionService:
        def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
            assert image_bytes
            assert content_type == "image/png"
            return ExtractedLabelFields(
                brand_name="OLD TOM DISTILLERY",
                class_type="Kentucky Straight Bourbon Whiskey",
                alcohol_percentage=45.0,
                net_contents="750 mL",
                origin_country="United States",
                has_government_warning=True,
                government_warning_text=(
                    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD "
                    "NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF "
                    "BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR "
                    "ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."
                ),
                government_warning_is_all_uppercase=True,
                government_warning_font_size_ratio=0.5,
                raw_text="sample",
            )

    monkeypatch.setattr("app.main._build_vision_service", lambda: DummyVisionService())

    response = client.post(
        "/api/v1/review",
        files={"image": ("label.png", b"fake-image", "image/png")},
        data={
            "expected_brand_name": "Old Tom Distillery",
            "expected_alcohol_percentage": "45",
            "expected_origin_country": "United States",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compliance"]["is_compliant"] is True
    assert body["extraction"]["brand_name"] == "OLD TOM DISTILLERY"
    assert body["extraction"]["ai_assisted_fields"] == []


def test_review_endpoint_service_failure(monkeypatch) -> None:
    def _raise_error() -> None:
        raise VisionExtractorError("config missing")

    monkeypatch.setattr("app.main._build_vision_service", _raise_error)

    response = client.post(
        "/api/v1/review",
        files={"image": ("label.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Vision extraction service unavailable"


def test_review_endpoint_flags_non_exact_warning_text(monkeypatch) -> None:
    class DummyVisionService:
        def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
            assert image_bytes
            assert content_type == "image/png"
            return ExtractedLabelFields(
                brand_name="OLD TOM DISTILLERY",
                class_type="Kentucky Straight Bourbon Whiskey",
                alcohol_percentage=45.0,
                net_contents="750 mL",
                origin_country="United States",
                has_government_warning=True,
                government_warning_text=(
                    "Government Warning: (1) According to the Surgeon General, women should "
                    "not drink alcoholic beverages during pregnancy because of the risk of "
                    "birth defects. (2) Consumption of alcoholic beverages impairs your "
                    "ability to drive a car or operate machinery, and may cause health problems."
                ),
                government_warning_is_all_uppercase=False,
                raw_text="sample",
            )

    monkeypatch.setattr("app.main._build_vision_service", lambda: DummyVisionService())

    response = client.post(
        "/api/v1/review",
        files={"image": ("label.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compliance"]["is_compliant"] is False
    assert (
        "Government warning header must be uppercase: 'GOVERNMENT WARNING'."
        in body["compliance"]["issues"]
    )


def test_review_endpoint_flags_small_government_warning_text(monkeypatch) -> None:
    class DummyVisionService:
        def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
            assert image_bytes
            assert content_type == "image/png"
            return ExtractedLabelFields(
                brand_name="OLD TOM DISTILLERY",
                class_type="Kentucky Straight Bourbon Whiskey",
                alcohol_percentage=45.0,
                net_contents="750 mL",
                origin_country="United States",
                has_government_warning=True,
                government_warning_text=(
                    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD "
                    "NOT DRINK ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF "
                    "BIRTH DEFECTS. (2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR "
                    "ABILITY TO DRIVE A CAR OR OPERATE MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."
                ),
                government_warning_is_all_uppercase=True,
                government_warning_font_size_ratio=0.25,
                raw_text="sample",
            )

    monkeypatch.setattr("app.main._build_vision_service", lambda: DummyVisionService())

    response = client.post(
        "/api/v1/review",
        files={"image": ("label.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compliance"]["is_compliant"] is False
    assert any(
        issue.startswith("Government warning text is too small relative to label text")
        for issue in body["compliance"]["issues"]
    )


def test_review_endpoint_flags_low_quality_ocr_signal(monkeypatch) -> None:
    class DummyVisionService:
        def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
            assert image_bytes
            assert content_type == "image/png"
            return ExtractedLabelFields(
                brand_name="BELL'S",
                class_type="IPA",
                alcohol_percentage=7.0,
                net_contents=None,
                origin_country=None,
                has_government_warning=False,
                government_warning_text=None,
                raw_text="BELL'S\nDEE\nTwo\nHearted\nIPA\n12 82\nAMERICAN IPA",
            )

    monkeypatch.setattr("app.main._build_vision_service", lambda: DummyVisionService())

    response = client.post(
        "/api/v1/review",
        files={"image": ("label.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["compliance"]["is_compliant"] is False
    assert (
        "Image quality may be poor (angle/lighting), OCR confidence appears low. "
        "Review manually." in body["compliance"]["issues"]
    )


def test_review_endpoint_handles_openai_fallback_failure_without_500(monkeypatch) -> None:
    class DummyVisionService:
        def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
            assert image_bytes
            assert content_type == "image/png"
            return ExtractedLabelFields(
                brand_name="TANQUERAY",
                class_type="EXPORT STRENGTH",
                alcohol_percentage=43.1,
                net_contents="700 cL",
                origin_country="United Kingdom",
                has_government_warning=False,
                government_warning_text=None,
                raw_text="WHOON DRY\nTanqueray\nEXPORT STRENGTH\nLONDON ORYZAN",
            )

    monkeypatch.setattr("app.main._build_vision_service", lambda: DummyVisionService())

    response = client.post(
        "/api/v1/review",
        files={"image": ("label.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200


def test_normalize_azure_openai_endpoint_preserves_responses_path() -> None:
    from app.main import _normalize_azure_openai_endpoint

    value = "https://treasury-app-2-resource.services.ai.azure.com/openai/v1/responses"
    assert _normalize_azure_openai_endpoint(value) == value


def test_normalize_azure_openai_endpoint_strips_generic_path() -> None:
    from app.main import _normalize_azure_openai_endpoint

    value = "https://example.openai.azure.com/openai/deployments/foo"
    assert value != _normalize_azure_openai_endpoint(value)
    assert _normalize_azure_openai_endpoint(value) == "https://example.openai.azure.com"


# --- ComplianceService rule unit tests -------------------------------------

COMPLIANT_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to drive "
    "a car or operate machinery, and may cause health problems."
)


def _make_fields(**overrides) -> ExtractedLabelFields:
    base = dict(
        brand_name="Old Tom Distillery",
        class_type="Kentucky Straight Bourbon Whiskey",
        alcohol_percentage=45.0,
        net_contents="750 mL",
        origin_country="United States",
        has_government_warning=True,
        government_warning_text=COMPLIANT_WARNING,
        government_warning_is_all_uppercase=True,
        government_warning_font_size_ratio=0.5,
        raw_text="Distilled and bottled by Old Tom Distillery, Bardstown, KY",
    )
    base.update(overrides)
    return ExtractedLabelFields(**base)


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues_detail}


def test_compliance_beer_missing_abv_is_not_an_error() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(
        class_type="American IPA",
        alcohol_percentage=None,
        net_contents="12 fl oz",
    )
    result = ComplianceService().evaluate(fields)

    assert "field.abv_missing" not in _codes(result)
    assert "field.abv_absent" in _codes(result)
    # Missing ABV alone must not make a beer non-compliant.
    assert result.is_compliant is True


def test_compliance_spirits_missing_abv_is_an_error() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(alcohol_percentage=None)
    result = ComplianceService().evaluate(fields)

    assert "field.abv_missing" in _codes(result)
    assert result.is_compliant is False


def test_compliance_wine_requires_sulfite_declaration() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(
        class_type="Cabernet Sauvignon",
        alcohol_percentage=13.5,
        raw_text="Produced and bottled by Napa Cellars, Napa, CA",
    )
    result = ComplianceService().evaluate(fields)
    assert "wine.sulfites" in _codes(result)
    assert result.is_compliant is False

    fields_ok = _make_fields(
        class_type="Cabernet Sauvignon",
        alcohol_percentage=13.5,
        raw_text="Produced and bottled by Napa Cellars, Napa, CA. CONTAINS SULFITES.",
    )
    assert "wine.sulfites" not in _codes(ComplianceService().evaluate(fields_ok))


def test_compliance_flags_missing_responsible_party() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(raw_text="Just a brand name and nothing else")
    result = ComplianceService().evaluate(fields)
    assert "field.responsible_party" in _codes(result)
    # Responsible-party detection is a warning, not a hard failure.
    assert result.is_compliant is True


def test_compliance_flags_non_standard_fill() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(net_contents="710 mL")
    result = ComplianceService().evaluate(fields)
    assert "field.standard_of_fill" in _codes(result)


def test_compliance_accepts_standard_fill() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(net_contents="750 mL")
    assert "field.standard_of_fill" not in _codes(ComplianceService().evaluate(fields))


def test_compliance_flags_implausible_abv_for_class() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(class_type="American IPA", alcohol_percentage=45.0)
    assert "abv.plausibility" in _codes(ComplianceService().evaluate(fields))


def test_compliance_flags_proof_abv_mismatch() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(
        alcohol_percentage=40.0,
        raw_text="Distilled by Old Tom Distillery, Bardstown, KY. 90 PROOF",
    )
    assert "spirits.proof_abv" in _codes(ComplianceService().evaluate(fields))

    fields_ok = _make_fields(
        alcohol_percentage=45.0,
        raw_text="Distilled by Old Tom Distillery, Bardstown, KY. 90 PROOF",
    )
    assert "spirits.proof_abv" not in _codes(ComplianceService().evaluate(fields_ok))


def test_compliance_flags_unrecognized_class() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(class_type="Mystery Elixir", alcohol_percentage=None)
    assert "class.unrecognized" in _codes(ComplianceService().evaluate(fields))


def test_compliance_issues_carry_severity_and_citation() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(has_government_warning=False)
    result = ComplianceService().evaluate(fields)
    warning_issue = next(i for i in result.issues_detail if i.code == "warning.missing")
    assert warning_issue.severity == "error"
    assert warning_issue.citation == "27 CFR 16.21"


def test_compliance_wine_abv_tolerance_widens_below_14() -> None:
    from app.services.compliance import ComplianceService

    fields = _make_fields(class_type="Chardonnay", alcohol_percentage=12.2)
    result = ComplianceService().evaluate(fields, expected_alcohol_percentage=13.5)
    # 1.3% difference is within the ±1.5% wine tolerance at/below 14%.
    assert "match.abv" not in _codes(result)
