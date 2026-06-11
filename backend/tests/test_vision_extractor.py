from typing import cast

import pytest
from azure.ai.vision.imageanalysis import ImageAnalysisClient

from app.schemas import ExtractedLabelFields
from app.services.vision_extractor import (
    AzureVisionReadExtractorService,
    HybridVisionExtractorService,
    VisionExtractorError,
)


class _FakeLine:
    def __init__(
        self,
        text: str,
        *,
        left: float = 140,
        width: float = 220,
        height: float = 28,
        top: float = 20,
    ) -> None:
        self.text = text
        self.bounding_polygon = [
            _FakePoint(left, top),
            _FakePoint(left + width, top),
            _FakePoint(left + width, top + height),
            _FakePoint(left, top + height),
        ]


class _FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y


class _FakeBlock:
    def __init__(self, lines: list[_FakeLine]) -> None:
        self.lines = lines


class _FakeReadResult:
    def __init__(self, blocks: list[_FakeBlock]) -> None:
        self.blocks = blocks


class _FakeAnalyzeResult:
    def __init__(self, read_result: _FakeReadResult) -> None:
        self.read = read_result


class _FakeClient:
    def __init__(self, lines: list[_FakeLine]) -> None:
        self._lines = lines

    def analyze(self, *, image_data: bytes, visual_features: list[object]) -> _FakeAnalyzeResult:
        assert image_data
        assert visual_features
        return _FakeAnalyzeResult(_FakeReadResult([_FakeBlock(self._lines)]))


class _StaticExtractor:
    def __init__(
        self,
        result: ExtractedLabelFields,
        *,
        last_brand_size_fallback_used: bool = False,
    ) -> None:
        self._result = result
        self.last_brand_size_fallback_used = last_brand_size_fallback_used

    def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
        assert image_bytes
        assert content_type.startswith("image/")
        return self._result


def test_azure_vision_extractor_maps_ocr_lines() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("OLD TOM DISTILLERY", width=360, height=56, top=22),
                _FakeLine("Kentucky Straight Bourbon Whiskey", width=300, height=30, top=86),
                _FakeLine("45% Alc./Vol. (90 Proof)", width=200, height=24, top=128),
                _FakeLine("750 mL", width=120, height=20, top=156),
                _FakeLine("Product of United States", width=210, height=20, top=180),
                _FakeLine(
                    (
                        "GOVERNMENT WARNING: (1) According to the Surgeon General, women should "
                        "not drink"
                    ),
                    width=430,
                    height=16,
                    top=210,
                ),
                _FakeLine(
                    "alcoholic beverages during pregnancy because of the risk of birth defects.",
                    width=420,
                    height=16,
                    top=232,
                ),
                _FakeLine(
                    "(2) Consumption of alcoholic beverages impairs your ability to drive a car",
                    width=418,
                    height=16,
                    top=252,
                ),
                _FakeLine(
                    "or operate machinery, and may cause health problems.",
                    width=410,
                    height=16,
                    top=272,
                ),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.brand_name == "OLD TOM DISTILLERY"
    assert result.class_type == "Kentucky Straight Bourbon Whiskey"
    assert result.alcohol_percentage == 45.0
    assert result.net_contents == "750 mL"
    assert result.origin_country == "United States"
    assert result.has_government_warning is True
    assert result.government_warning_text is not None
    assert result.government_warning_text.startswith("GOVERNMENT WARNING")


def test_azure_vision_extractor_extracts_net_contents_in_centiliters() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("TANQUERAY", width=260, height=44, top=20),
                _FakeLine("LONDON DRY GIN", width=220, height=30, top=78),
                _FakeLine("70cl", width=120, height=20, top=132),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.net_contents == "70 cL"


def test_azure_vision_extractor_extracts_net_contents_in_fl_oz() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("CRAFT LAGER", width=240, height=40, top=20),
                _FakeLine("12 fl oz", width=140, height=22, top=90),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.net_contents == "12 fl oz"


def test_azure_vision_extractor_raises_when_no_text() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(ImageAnalysisClient, _FakeClient([]))

    with pytest.raises(VisionExtractorError, match="No text detected in image"):
        service.extract_from_image(b"fake-image", "image/png")


def test_azure_vision_extractor_ignores_established_line_for_brand() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("ESTABLISHED 1812", width=240, height=22, top=24),
                _FakeLine("BARREL HOUSE", width=340, height=54, top=52),
                _FakeLine("Small Batch Bourbon Whiskey", width=300, height=30, top=114),
                _FakeLine("45% Alc./Vol. (90 Proof)", width=220, height=22, top=146),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.brand_name == "BARREL HOUSE"


def test_azure_vision_extractor_infers_country_from_city_state() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("RIVER POINT", width=260, height=44, top=20),
                _FakeLine("Craft Gin", width=180, height=24, top=78),
                _FakeLine("Nashville, TN", width=170, height=22, top=112),
                _FakeLine("40% Alc./Vol.", width=160, height=22, top=142),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.origin_country == "United States"


def test_azure_vision_extractor_infers_country_from_region_text() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("VIGNA DEL SOLE", width=280, height=42, top=20),
                _FakeLine("Rosso", width=120, height=22, top=72),
                _FakeLine("Tuscany", width=138, height=22, top=102),
                _FakeLine("13.5% Alc./Vol.", width=170, height=22, top=132),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.origin_country == "Italy"


def test_azure_vision_extractor_infers_country_from_american_style_text() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("TWO HEARTED", width=250, height=36, top=20),
                _FakeLine("AMERICAN IPA", width=220, height=28, top=74),
                _FakeLine("7.0% Alc./Vol.", width=165, height=22, top=112),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.origin_country == "United States"


def test_azure_vision_extractor_infers_country_from_london_text() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("LONDON DRY GIN", width=240, height=32, top=20),
                _FakeLine("Distilled in London", width=220, height=24, top=66),
                _FakeLine("40% Alc./Vol.", width=170, height=22, top=102),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.origin_country == "United Kingdom"


def test_azure_vision_extractor_matches_beer_style_class_type() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("HARBOR LIGHT", width=300, height=46, top=20),
                _FakeLine("Hazy IPA", width=180, height=26, top=78),
                _FakeLine("6.8% Alc./Vol.", width=170, height=22, top=110),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.class_type == "Hazy IPA"


def test_azure_vision_extractor_ignores_corner_logo_text() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("CANADIAN BREWING AWARDS", left=0, width=170, height=28, top=8),
                _FakeLine("BOXING ROCK", left=150, width=210, height=34, top=42),
                _FakeLine("IPA", left=160, width=320, height=95, top=88),
                _FakeLine("INDIA PALE ALE", left=168, width=250, height=30, top=198),
                _FakeLine("STRONG BEER 6.6% ALC./VOL", left=166, width=280, height=24, top=240),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.brand_name == "BOXING ROCK"
    assert result.class_type in {"IPA", "INDIA PALE ALE"}
    assert result.class_type != result.brand_name


def test_azure_vision_extractor_uses_second_largest_when_style_is_largest() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("SUMMERFIELD BREWING", left=160, width=220, height=34, top=32),
                _FakeLine("IPA", left=150, width=300, height=110, top=72),
                _FakeLine("INDIA PALE ALE", left=164, width=240, height=28, top=196),
                _FakeLine("6.4% Alc./Vol.", left=166, width=210, height=22, top=232),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.class_type in {"IPA", "INDIA PALE ALE"}
    assert result.brand_name == "SUMMERFIELD BREWING"
    assert result.brand_name != result.class_type


def test_azure_vision_extractor_prefers_taller_brand_over_thin_footer() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("BOXING ROCK", left=160, width=220, height=34, top=48),
                _FakeLine("IPA", left=150, width=300, height=112, top=86),
                _FakeLine("INDIA PALE ALE", left=162, width=246, height=30, top=206),
                _FakeLine(
                    "STRONG BEER 6.6% ALC./VOL 473ML",
                    left=132,
                    width=380,
                    height=18,
                    top=254,
                ),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.class_type in {"IPA", "INDIA PALE ALE"}
    assert result.brand_name == "BOXING ROCK"
    assert result.brand_name != result.class_type


def test_azure_vision_extractor_combines_stacked_same_height_brand_lines() -> None:
    service = AzureVisionReadExtractorService(
        endpoint="https://example.cognitiveservices.azure.com",
        api_key="dummy-key",
    )
    service._client = cast(
        ImageAnalysisClient,
        _FakeClient(
            [
                _FakeLine("BOXING", left=172, width=164, height=30, top=52),
                _FakeLine("ROCK", left=176, width=156, height=30, top=84),
                _FakeLine("IPA", left=150, width=300, height=112, top=120),
                _FakeLine("INDIA PALE ALE", left=162, width=246, height=30, top=236),
            ]
        ),
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.brand_name == "BOXING ROCK"
    assert result.class_type in {"IPA", "INDIA PALE ALE"}
    assert result.brand_name != result.class_type


def test_hybrid_extractor_backfills_missing_core_data() -> None:
    primary = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="TWO HEARTED",
            class_type="IPA",
            alcohol_percentage=7.0,
            net_contents=None,
            origin_country=None,
            has_government_warning=False,
            government_warning_text=None,
            raw_text="TWO HEARTED\nIPA\n7.0%",
        )
    )
    fallback = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="TWO HEARTED ALE",
            class_type="AMERICAN IPA",
            alcohol_percentage=7.0,
            net_contents="355 mL",
            origin_country="United States",
            has_government_warning=True,
            government_warning_text="GOVERNMENT WARNING: ...",
            raw_text="fallback",
        )
    )

    service = HybridVisionExtractorService(
        primary=primary,
        fallback=fallback,
        fallback_on_missing_core_data=True,
        fallback_on_low_quality=False,
    )

    result = service.extract_from_image(b"fake-image", "image/png")

    assert result.brand_name == "TWO HEARTED"
    assert result.net_contents == "355 mL"
    assert result.origin_country == "United States"
    assert result.has_government_warning is True


def test_hybrid_extractor_prefers_fallback_when_low_quality() -> None:
    primary = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="DEE",
            class_type="IPA",
            alcohol_percentage=7.0,
            net_contents="355 mL",
            origin_country=None,
            has_government_warning=False,
            government_warning_text=None,
            raw_text="BELL'S\nDEE\nTwo\nHearted\nIPA\n12 82\nAMERICAN IPA",
        )
    )
    fallback = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="TWO HEARTED",
            class_type="AMERICAN IPA",
            alcohol_percentage=7.0,
            net_contents="355 mL",
            origin_country="United States",
            has_government_warning=False,
            government_warning_text=None,
            raw_text="fallback",
        )
    )

    service = HybridVisionExtractorService(
        primary=primary,
        fallback=fallback,
        fallback_on_missing_core_data=False,
        fallback_on_low_quality=True,
    )

    result = service.extract_from_image(b"fake-image", "image/jpeg")

    assert result.brand_name == "TWO HEARTED"
    assert result.class_type == "AMERICAN IPA"
    assert result.origin_country == "United States"


def test_hybrid_extractor_uses_fallback_when_size_heuristic_was_used() -> None:
    primary = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="IPA",
            class_type="INDIA PALE ALE",
            alcohol_percentage=6.6,
            net_contents="473 mL",
            origin_country=None,
            has_government_warning=False,
            government_warning_text=None,
            raw_text="BOXING\nROCK\nIPA\nINDIA PALE ALE",
        ),
        last_brand_size_fallback_used=True,
    )
    fallback = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="BOXING ROCK",
            class_type="INDIA PALE ALE",
            alcohol_percentage=6.6,
            net_contents="473 mL",
            origin_country="Canada",
            has_government_warning=False,
            government_warning_text=None,
            raw_text="fallback",
        )
    )

    service = HybridVisionExtractorService(
        primary=primary,
        fallback=fallback,
        fallback_on_missing_core_data=False,
        fallback_on_low_quality=False,
        fallback_on_size_heuristic_selection=True,
    )

    result = service.extract_from_image(b"fake-image", "image/jpeg")

    assert result.brand_name == "BOXING ROCK"
    assert result.origin_country == "Canada"


def test_hybrid_extractor_repairs_unknown_class_type_from_text_context() -> None:
    primary = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="TANQUERAY",
            class_type="EXPORT STRENGTH",
            alcohol_percentage=43.1,
            net_contents="700 mL",
            origin_country="United Kingdom",
            has_government_warning=False,
            government_warning_text=None,
            raw_text="WHOON DRY\nTanqueray\nEXPORT STRENGTH\nLONDON DRY GIN",
        )
    )
    fallback = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="TANQUERAY",
            class_type="EXPORT STRENGTH",
            alcohol_percentage=43.1,
            net_contents="700 mL",
            origin_country="United Kingdom",
            has_government_warning=False,
            government_warning_text=None,
            raw_text="WHOON DRY\nTanqueray\nEXPORT STRENGTH\nLONDON DRY GIN",
        )
    )

    service = HybridVisionExtractorService(
        primary=primary,
        fallback=fallback,
        fallback_on_missing_core_data=True,
        fallback_on_low_quality=False,
        fallback_on_size_heuristic_selection=False,
    )

    result = service.extract_from_image(b"fake-image", "image/jpeg")

    assert result.class_type == "London Dry Gin"


def test_hybrid_extractor_uses_gin_brand_hint_when_text_is_noisy() -> None:
    primary = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="Tanqueray",
            class_type="EXPORT STRENGTH",
            alcohol_percentage=43.1,
            net_contents="700 mL",
            origin_country="United Kingdom",
            has_government_warning=False,
            government_warning_text=None,
            raw_text="WHOON DRY\nTanqueray\nEXPORT STRENGTH\nDISTILLED TOUR TIMEE\nLONDON ORYZAN",
        )
    )
    fallback = _StaticExtractor(
        ExtractedLabelFields(
            brand_name="Tanqueray",
            class_type="EXPORT STRENGTH",
            alcohol_percentage=43.1,
            net_contents="700 mL",
            origin_country="United Kingdom",
            has_government_warning=False,
            government_warning_text=None,
            raw_text="fallback",
        )
    )

    service = HybridVisionExtractorService(
        primary=primary,
        fallback=fallback,
        fallback_on_missing_core_data=True,
        fallback_on_low_quality=False,
        fallback_on_size_heuristic_selection=False,
    )

    result = service.extract_from_image(b"fake-image", "image/jpeg")

    assert result.class_type == "Gin"
