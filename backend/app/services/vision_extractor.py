import logging
import re
from base64 import b64encode
from dataclasses import dataclass
from typing import Any, Protocol, cast

from azure.ai.vision.imageanalysis import ImageAnalysisClient
from azure.ai.vision.imageanalysis.models import VisualFeatures
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import HttpResponseError, ServiceRequestError
from azure.identity import DefaultAzureCredential
from openai import APIConnectionError, APIStatusError, AzureOpenAI, OpenAI
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.schemas import ExtractedLabelFields

logger = logging.getLogger(__name__)

ALCOHOL_CLASS_KEYWORDS = {
    "beer": {
        "beer",
        "lager",
        "pilsner",
        "pale ale",
        "ipa",
        "india pale ale",
        "double ipa",
        "imperial ipa",
        "session ipa",
        "hazy ipa",
        "new england ipa",
        "west coast ipa",
        "stout",
        "porter",
        "brown ale",
        "amber ale",
        "red ale",
        "blonde ale",
        "golden ale",
        "cream ale",
        "wheat beer",
        "hefeweizen",
        "witbier",
        "saison",
        "farmhouse ale",
        "kolsch",
        "gose",
        "sour ale",
        "lambic",
        "dubbel",
        "tripel",
        "quadrupel",
        "bock",
        "doppelbock",
        "maibock",
        "marzen",
        "oktoberfest",
        "schwarzbier",
        "smoked beer",
        "barleywine",
    },
    "spirits": {
        "whiskey",
        "whisky",
        "bourbon",
        "rye",
        "scotch",
        "single malt",
        "blended whisky",
        "irish whiskey",
        "tennessee whiskey",
        "canadian whisky",
        "corn whiskey",
        "moonshine",
        "vodka",
        "gin",
        "dry gin",
        "london dry gin",
        "old tom gin",
        "rum",
        "white rum",
        "gold rum",
        "dark rum",
        "spiced rum",
        "rhum agricole",
        "cachaca",
        "tequila",
        "mezcal",
        "sotol",
        "raicilla",
        "brandy",
        "cognac",
        "armagnac",
        "grappa",
        "pisco",
        "calvados",
        "liqueur",
        "amaro",
        "aperitif",
        "digestif",
        "absinthe",
        "schnapps",
        "aquavit",
        "baijiu",
        "soju",
        "shochu",
    },
    "wine": {
        "wine",
        "red wine",
        "white wine",
        "rose",
        "rose wine",
        "sparkling wine",
        "champagne",
        "prosecco",
        "cava",
        "still wine",
        "dessert wine",
        "fortified wine",
        "port",
        "sherry",
        "madeira",
        "vermouth",
        "riesling",
        "chardonnay",
        "sauvignon blanc",
        "pinot grigio",
        "chenin blanc",
        "gewurztraminer",
        "pinot noir",
        "cabernet sauvignon",
        "merlot",
        "syrah",
        "shiraz",
        "malbec",
        "zinfandel",
        "tempranillo",
        "sangiovese",
    },
    "other": {
        "hard cider",
        "cider",
        "perry",
        "mead",
        "sake",
        "rice wine",
        "hard seltzer",
        "ready to drink",
        "rtd",
        "cooler",
        "flavored malt beverage",
        "kombucha",
    },
}

ALCOHOL_CLASS_NORMALIZED = {
    re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip()
    for keywords in ALCOHOL_CLASS_KEYWORDS.values()
    for keyword in keywords
}

PROMPT = """
You are reviewing alcohol beverage label images for U.S. TTB compliance support.
Extract fields and return JSON only using this exact schema:
{
  "brand_name": "string",
  "class_type": "string|null",
  "alcohol_percentage": "number|null",
  "net_contents": "string|null",
  "origin_country": "string|null",
  "has_government_warning": "boolean",
  "government_warning_text": "string|null",
  "raw_text": "string|null"
}
If a field is not visible, return null for that field.
Return only valid JSON without markdown.
""".strip()


class VisionExtractorError(RuntimeError):
    """Raised when label extraction fails after retries."""


class VisionExtractor(Protocol):
    def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields: ...


class CountryResolver(Protocol):
    def infer_country_from_location_text(self, raw_text: str) -> tuple[str | None, float]: ...


class HybridVisionExtractorService:
    def __init__(
        self,
        primary: VisionExtractor,
        fallback: VisionExtractor,
        fallback_on_missing_core_data: bool = True,
        fallback_on_low_quality: bool = True,
        fallback_on_size_heuristic_selection: bool = True,
        fallback_on_unknown_class_type: bool = True,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._fallback_on_missing_core_data = fallback_on_missing_core_data
        self._fallback_on_low_quality = fallback_on_low_quality
        self._fallback_on_size_heuristic_selection = fallback_on_size_heuristic_selection
        self._fallback_on_unknown_class_type = fallback_on_unknown_class_type

    def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
        primary_result = self._primary.extract_from_image(image_bytes, content_type)

        missing_core_data = self._fallback_on_missing_core_data and self._has_missing_core_data(
            primary_result
        )
        low_quality = self._fallback_on_low_quality and self._has_low_quality_ocr_signal(
            primary_result.raw_text
        )
        size_heuristic_selection = (
            self._fallback_on_size_heuristic_selection and self._used_size_heuristic_selection()
        )
        unknown_class_type = self._fallback_on_unknown_class_type and self._has_unknown_class_type(
            primary_result.class_type
        )

        if not (missing_core_data or low_quality or size_heuristic_selection or unknown_class_type):
            return primary_result

        try:
            fallback_result = self._fallback.extract_from_image(image_bytes, content_type)
        except (VisionExtractorError, APIConnectionError, APIStatusError):
            logger.warning("vision_fallback_extraction_failed")
            return primary_result

        logger.info(
            "vision_fallback_applied",
            extra={
                "missing_core_data": missing_core_data,
                "low_quality": low_quality,
                "size_heuristic_selection": size_heuristic_selection,
                "unknown_class_type": unknown_class_type,
            },
        )
        return self._merge_results(
            primary=primary_result,
            fallback=fallback_result,
            prefer_fallback=(low_quality or size_heuristic_selection),
        )

    def _has_unknown_class_type(self, class_type: str | None) -> bool:
        if class_type is None:
            return False
        return not self._is_known_class_type(class_type)

    def _used_size_heuristic_selection(self) -> bool:
        marker = getattr(self._primary, "last_brand_size_fallback_used", False)
        return isinstance(marker, bool) and marker

    def _has_missing_core_data(self, extracted: ExtractedLabelFields) -> bool:
        return any(
            (
                not extracted.brand_name.strip(),
                extracted.class_type is None,
                extracted.alcohol_percentage is None,
                extracted.origin_country is None,
            )
        )

    def _has_low_quality_ocr_signal(self, raw_text: str | None) -> bool:
        if raw_text is None:
            return False

        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            return False

        token_pattern = re.compile(r"[A-Za-z0-9%./]+")
        tokens = token_pattern.findall(raw_text)

        short_token_count = sum(
            1 for token in tokens if token.isalpha() and len(token) <= 3 and token.lower() != "ipa"
        )
        numeric_fragment_count = sum(1 for line in lines if re.fullmatch(r"[\d\s.,%/]+", line))
        mixed_noise_count = sum(
            1
            for token in tokens
            if any(ch.isalpha() for ch in token)
            and any(ch.isdigit() for ch in token)
            and len(token) <= 5
        )

        has_fragmented_text = len(lines) >= 6 and short_token_count >= max(3, len(tokens) // 3)
        has_numeric_noise = numeric_fragment_count >= 1 and len(lines) >= 4
        has_compact_mixed_noise = mixed_noise_count >= 2

        return has_fragmented_text or (has_numeric_noise and has_compact_mixed_noise)

    def _merge_results(
        self,
        *,
        primary: ExtractedLabelFields,
        fallback: ExtractedLabelFields,
        prefer_fallback: bool,
    ) -> ExtractedLabelFields:
        def choose_text(
            primary_value: str | None,
            fallback_value: str | None,
        ) -> tuple[str | None, bool]:
            fallback_present = fallback_value is not None and fallback_value.strip() != ""
            primary_present = primary_value is not None and primary_value.strip() != ""

            if prefer_fallback and fallback_present:
                return fallback_value, True
            if primary_present:
                return primary_value, False
            if fallback_present:
                return fallback_value, True
            return primary_value, False

        merged_brand_candidate, brand_from_fallback = choose_text(
            primary.brand_name,
            fallback.brand_name,
        )
        merged_brand = merged_brand_candidate or primary.brand_name
        merged_raw_text, _ = choose_text(primary.raw_text, fallback.raw_text)
        merged_class, class_from_fallback = self._choose_class_type(
            primary_class=primary.class_type,
            fallback_class=fallback.class_type,
            raw_text=merged_raw_text,
            brand_name=merged_brand,
            prefer_fallback=prefer_fallback,
        )
        # Net contents must be OCR-derived only; do not infer/guess via AI fallback.
        merged_net = primary.net_contents
        merged_origin, origin_from_fallback = choose_text(
            primary.origin_country,
            fallback.origin_country,
        )
        merged_warning_text, warning_text_from_fallback = choose_text(
            primary.government_warning_text,
            fallback.government_warning_text,
        )
        merged_has_warning = primary.has_government_warning or fallback.has_government_warning
        warning_from_fallback = (
            not primary.has_government_warning
        ) and fallback.has_government_warning

        merged_alcohol = primary.alcohol_percentage
        alcohol_from_fallback = False
        if merged_alcohol is None or (prefer_fallback and fallback.alcohol_percentage is not None):
            merged_alcohol = fallback.alcohol_percentage
            alcohol_from_fallback = fallback.alcohol_percentage is not None

        ai_assisted_fields: set[str] = set(primary.ai_assisted_fields) | set(
            fallback.ai_assisted_fields
        )
        if brand_from_fallback:
            ai_assisted_fields.add("brand_name")
        if class_from_fallback:
            ai_assisted_fields.add("class_type")
        if alcohol_from_fallback:
            ai_assisted_fields.add("alcohol_percentage")
        if origin_from_fallback:
            ai_assisted_fields.add("origin_country")
        if warning_text_from_fallback:
            ai_assisted_fields.add("government_warning_text")
        if warning_from_fallback:
            ai_assisted_fields.add("has_government_warning")

        return ExtractedLabelFields(
            brand_name=merged_brand,
            class_type=merged_class,
            alcohol_percentage=merged_alcohol,
            net_contents=merged_net,
            origin_country=merged_origin,
            has_government_warning=merged_has_warning,
            government_warning_text=merged_warning_text,
            raw_text=merged_raw_text,
            ai_assisted_fields=sorted(ai_assisted_fields),
        )

    def _choose_class_type(
        self,
        *,
        primary_class: str | None,
        fallback_class: str | None,
        raw_text: str | None,
        brand_name: str,
        prefer_fallback: bool,
    ) -> tuple[str | None, bool]:
        primary_known = self._is_known_class_type(primary_class)
        fallback_known = self._is_known_class_type(fallback_class)

        if prefer_fallback and fallback_known:
            return fallback_class, True
        if primary_known:
            return primary_class, False
        if fallback_known:
            return fallback_class, True

        derived = self._derive_class_type_from_context(raw_text=raw_text, brand_name=brand_name)
        if derived is not None:
            return derived, False

        if prefer_fallback and fallback_class is not None:
            return fallback_class, True
        return (primary_class or fallback_class), (
            primary_class is None and fallback_class is not None
        )

    def _is_known_class_type(self, value: str | None) -> bool:
        if value is None:
            return False

        normalized = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        if not normalized:
            return False

        haystack = f" {normalized} "

        for keywords in ALCOHOL_CLASS_KEYWORDS.values():
            for keyword in keywords:
                keyword_normalized = re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip()
                if keyword_normalized and f" {keyword_normalized} " in haystack:
                    return True
        return False

    def _derive_class_type_from_context(self, raw_text: str | None, brand_name: str) -> str | None:
        normalized_text = ""
        if raw_text is not None:
            normalized_text = re.sub(r"[^a-z0-9]+", " ", raw_text.lower()).strip()

        haystack = f" {normalized_text} "

        best_keyword: str | None = None
        best_word_count = 0
        for keywords in ALCOHOL_CLASS_KEYWORDS.values():
            for keyword in keywords:
                keyword_normalized = re.sub(r"[^a-z0-9]+", " ", keyword.lower()).strip()
                if not keyword_normalized:
                    continue
                if f" {keyword_normalized} " in haystack:
                    word_count = len(keyword_normalized.split())
                    if word_count > best_word_count:
                        best_keyword = keyword
                        best_word_count = word_count

        if best_keyword is not None:
            return best_keyword.title()

        normalized_brand = re.sub(r"[^a-z0-9]+", " ", brand_name.lower()).strip()
        gin_brand_hints = (
            "tanqueray",
            "bombay",
            "beefeater",
            "hendrick",
            "gordon",
            "sipsmith",
        )
        if any(hint in normalized_brand for hint in gin_brand_hints):
            return "Gin"

        return None


@dataclass(frozen=True)
class OcrLineCandidate:
    text: str
    polygon_area: float
    box_width: float
    box_height: float
    min_y: float
    center_x: float
    center_y: float


class AzureOpenAIVisionExtractorService:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
    ) -> None:
        self._deployment = deployment
        self._uses_responses_api = endpoint.rstrip("/").endswith("/openai/v1/responses")
        if self._uses_responses_api:
            responses_base_url = endpoint.rstrip("/")
            if responses_base_url.endswith("/responses"):
                responses_base_url = responses_base_url[: -len("/responses")]
            self._client = OpenAI(
                base_url=responses_base_url,
                api_key=api_key,
            )
        else:
            self._client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type((APIConnectionError, APIStatusError, VisionExtractorError)),
        reraise=True,
    )
    def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
        """Fallback extractor when Azure Vision OCR is unavailable in a region."""
        encoded_image = b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{content_type};base64,{encoded_image}"

        logger.info("vision_extraction_started")
        content: str | None = None
        try:
            if self._uses_responses_api:
                input_payload: list[dict[str, object]] = [
                    {
                        "role": "system",
                        "content": [
                            {
                                "type": "input_text",
                                "text": "You are an accurate extraction engine.",
                            }
                        ],
                    },
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": PROMPT},
                            {"type": "input_image", "image_url": data_url},
                        ],
                    },
                ]
                completion_response = self._client.responses.create(
                    model=self._deployment,
                    input=cast(Any, input_payload),
                )
                content = self._extract_responses_text(completion_response)
            else:
                chat_completion = self._client.chat.completions.create(
                    model=self._deployment,
                    temperature=0,
                    max_tokens=800,
                    response_format={"type": "json_object"},
                    messages=[
                        {"role": "system", "content": "You are an accurate extraction engine."},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": PROMPT},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": data_url,
                                    },
                                },
                            ],
                        },
                    ],
                )
                content = chat_completion.choices[0].message.content
        except (APIConnectionError, APIStatusError):
            logger.exception("vision_extraction_transient_failure")
            raise

        if content is None:
            raise VisionExtractorError("Model returned no content")

        parsed = _parse_model_output(content)
        result = ExtractedLabelFields.model_validate(parsed)
        ai_assisted_fields: list[str] = []
        if result.brand_name.strip():
            ai_assisted_fields.append("brand_name")
        if result.class_type is not None and result.class_type.strip():
            ai_assisted_fields.append("class_type")
        if result.alcohol_percentage is not None:
            ai_assisted_fields.append("alcohol_percentage")
        if result.net_contents is not None and result.net_contents.strip():
            ai_assisted_fields.append("net_contents")
        if result.origin_country is not None and result.origin_country.strip():
            ai_assisted_fields.append("origin_country")
        if result.has_government_warning:
            ai_assisted_fields.append("has_government_warning")
        if result.government_warning_text is not None and result.government_warning_text.strip():
            ai_assisted_fields.append("government_warning_text")
        result = result.model_copy(update={"ai_assisted_fields": ai_assisted_fields})
        logger.info(
            "vision_extraction_completed",
            extra={"has_warning": result.has_government_warning},
        )
        return result

    def _extract_responses_text(self, completion: object) -> str | None:
        output_text = getattr(completion, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        model_dump = getattr(completion, "model_dump", None)
        if not callable(model_dump):
            return None

        payload = model_dump()
        if not isinstance(payload, dict):
            return None

        output_items = payload.get("output")
        if not isinstance(output_items, list):
            return None

        text_parts: list[str] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                if not isinstance(content_item, dict):
                    continue
                text_value = content_item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    text_parts.append(text_value)

        if not text_parts:
            return None
        return "\n".join(text_parts)


class AzureOpenAICountryResolver:
    def __init__(
        self,
        endpoint: str,
        api_key: str,
        deployment: str,
        api_version: str,
    ) -> None:
        self._deployment = deployment
        self._uses_responses_api = endpoint.rstrip("/").endswith("/openai/v1/responses")
        if self._uses_responses_api:
            responses_base_url = endpoint.rstrip("/")
            if responses_base_url.endswith("/responses"):
                responses_base_url = responses_base_url[: -len("/responses")]
            self._client = OpenAI(
                base_url=responses_base_url,
                api_key=api_key,
            )
        else:
            self._client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=api_version,
            )

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.2, min=0.2, max=0.8),
        retry=retry_if_exception_type((APIConnectionError, APIStatusError, VisionExtractorError)),
        reraise=True,
    )
    def infer_country_from_location_text(self, raw_text: str) -> tuple[str | None, float]:
        prompt = (
            "Infer a country from location text. Return JSON only with keys: "
            "country (string or null), confidence (0 to 1). "
            "If uncertain, return null country with low confidence.\n"
            f"Location text:\n{raw_text}"
        )

        content: str | None = None
        if self._uses_responses_api:
            input_payload: list[dict[str, object]] = [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "You normalize location text to country.",
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt}],
                },
            ]
            completion_response = self._client.responses.create(
                model=self._deployment,
                input=cast(Any, input_payload),
            )
            content = self._extract_responses_text(completion_response)
        else:
            chat_completion = self._client.chat.completions.create(
                model=self._deployment,
                temperature=0,
                max_tokens=120,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": "You normalize location text to country."},
                    {"role": "user", "content": prompt},
                ],
            )
            content = chat_completion.choices[0].message.content

        if content is None:
            raise VisionExtractorError("Country resolver returned no content")

        parsed = _parse_model_output(content)
        country = parsed.get("country")
        confidence = parsed.get("confidence")

        if country is not None and not isinstance(country, str):
            country = None
        if not isinstance(confidence, int | float):
            confidence = 0.0

        return country, float(max(0.0, min(1.0, confidence)))

    def _extract_responses_text(self, completion: object) -> str | None:
        output_text = getattr(completion, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        model_dump = getattr(completion, "model_dump", None)
        if not callable(model_dump):
            return None

        payload = model_dump()
        if not isinstance(payload, dict):
            return None

        output_items = payload.get("output")
        if not isinstance(output_items, list):
            return None

        text_parts: list[str] = []
        for item in output_items:
            if not isinstance(item, dict):
                continue
            content_items = item.get("content")
            if not isinstance(content_items, list):
                continue
            for content_item in content_items:
                if not isinstance(content_item, dict):
                    continue
                text_value = content_item.get("text")
                if isinstance(text_value, str) and text_value.strip():
                    text_parts.append(text_value)

        if not text_parts:
            return None
        return "\n".join(text_parts)


def _parse_model_output(model_output: str) -> dict[str, object]:
    import json

    try:
        data = json.loads(model_output)
    except json.JSONDecodeError as exc:
        logger.warning("vision_extraction_invalid_json")
        raise VisionExtractorError("Model output was not valid JSON") from exc

    if not isinstance(data, dict):
        raise VisionExtractorError("Model output was not an object")
    # The model occasionally omits brand_name or returns null when a label is
    # unreadable. brand_name is a required non-null field, so coerce it to an
    # empty string here; the compliance engine then flags it as missing rather
    # than the request failing with a 500.
    brand_name = data.get("brand_name")
    if not isinstance(brand_name, str):
        data["brand_name"] = ""
    return data


class AzureVisionReadExtractorService:
    def __init__(
        self,
        endpoint: str,
        api_key: str | None = None,
        country_resolver: CountryResolver | None = None,
    ) -> None:
        credential: AzureKeyCredential | DefaultAzureCredential
        if api_key:
            credential = AzureKeyCredential(api_key)
        else:
            credential = DefaultAzureCredential()
        self._country_resolver = country_resolver
        self.last_brand_size_fallback_used = False
        self._client = ImageAnalysisClient(
            endpoint=endpoint,
            credential=credential,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
        retry=retry_if_exception_type(
            (ServiceRequestError, HttpResponseError, VisionExtractorError)
        ),
        reraise=True,
    )
    def extract_from_image(self, image_bytes: bytes, content_type: str) -> ExtractedLabelFields:
        del content_type

        logger.info("vision_ocr_started")
        try:
            result = self._client.analyze(
                image_data=image_bytes,
                visual_features=[VisualFeatures.READ],
            )
        except (ServiceRequestError, HttpResponseError):
            logger.exception("vision_ocr_transient_failure")
            raise

        lines: list[OcrLineCandidate] = []
        if result.read is not None and result.read.blocks is not None:
            for block in result.read.blocks:
                if block.lines is None:
                    continue
                for line in block.lines:
                    if line.text is not None and line.text.strip():
                        points = self._extract_polygon_points(
                            getattr(line, "bounding_polygon", None)
                        )
                        lines.append(
                            OcrLineCandidate(
                                text=line.text.strip(),
                                polygon_area=self._polygon_area(points),
                                box_width=self._box_width(points),
                                box_height=self._box_height(points),
                                min_y=self._min_y(points),
                                center_x=self._center_x(points),
                                center_y=self._center_y(points),
                            )
                        )

        if not lines:
            raise VisionExtractorError("No text detected in image")

        filtered_lines = self._filter_to_bottle_region(lines)
        extraction_lines = filtered_lines if filtered_lines else lines

        text_lines = [line.text for line in extraction_lines]
        all_text_lines = [line.text for line in lines]
        raw_text = "\n".join(all_text_lines)
        (
            has_government_warning,
            government_warning_text,
            government_warning_is_all_uppercase,
            government_warning_font_size_ratio,
        ) = self._extract_government_warning_metadata(lines)
        class_type = self._extract_class_type(text_lines)
        brand_name, used_size_fallback = self._extract_brand_name(
            extraction_lines,
            disallowed_values=[class_type] if class_type is not None else [],
        )
        self.last_brand_size_fallback_used = used_size_fallback
        extracted = ExtractedLabelFields(
            brand_name=brand_name,
            class_type=class_type,
            alcohol_percentage=self._extract_alcohol_percentage(raw_text),
            net_contents=self._extract_net_contents(raw_text),
            origin_country=self._extract_origin_country(raw_text),
            has_government_warning=has_government_warning,
            government_warning_text=government_warning_text,
            government_warning_is_all_uppercase=government_warning_is_all_uppercase,
            government_warning_font_size_ratio=government_warning_font_size_ratio,
            raw_text=raw_text,
        )
        logger.info(
            "vision_ocr_completed",
            extra={"line_count": len(lines), "filtered_line_count": len(extraction_lines)},
        )
        return extracted

    def _filter_to_bottle_region(self, lines: list[OcrLineCandidate]) -> list[OcrLineCandidate]:
        if len(lines) < 3:
            return lines

        weighted_center_x = self._weighted_median(
            values=[line.center_x for line in lines],
            weights=[max(line.polygon_area, 1.0) for line in lines],
        )

        deviations = [abs(line.center_x - weighted_center_x) for line in lines]
        mad = self._median(deviations)
        x_threshold = max(90.0, mad * 2.8)

        narrowed = [line for line in lines if abs(line.center_x - weighted_center_x) <= x_threshold]

        if len(narrowed) >= max(2, len(lines) // 2):
            return narrowed
        return lines

    def _weighted_median(self, values: list[float], weights: list[float]) -> float:
        ordered = sorted(zip(values, weights, strict=False), key=lambda item: item[0])
        total_weight = sum(max(weight, 0.0) for _, weight in ordered)
        if total_weight <= 0:
            return self._median(values)

        cumulative = 0.0
        midpoint = total_weight / 2
        for value, weight in ordered:
            cumulative += max(weight, 0.0)
            if cumulative >= midpoint:
                return value
        return ordered[-1][0]

    def _median(self, values: list[float]) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 0:
            return (ordered[mid - 1] + ordered[mid]) / 2
        return ordered[mid]

    def _extract_brand_name(
        self,
        lines: list[OcrLineCandidate],
        disallowed_values: list[str] | None = None,
    ) -> tuple[str, bool]:
        brand_lines = self._augment_stacked_brand_candidates(lines)
        best_candidate: str | None = None
        best_score = float("-inf")
        total_lines = len(brand_lines)
        max_area = max((line.polygon_area for line in brand_lines), default=0.0)
        max_height = max((line.box_height for line in brand_lines), default=1.0)
        min_top = min((line.min_y for line in brand_lines), default=0.0)
        max_top = max((line.min_y for line in brand_lines), default=0.0)
        vertical_span = max(1.0, max_top - min_top)
        disallowed_normalized = {
            self._normalize_text_for_match(value)
            for value in (disallowed_values or [])
            if value is not None and value.strip()
        }

        # If the largest on-bottle text is a style/type (e.g. IPA), choose the
        # most visually prominent eligible text block as brand to avoid duplicate tagging.
        by_area = sorted(brand_lines, key=lambda line: line.polygon_area, reverse=True)
        if by_area:
            largest_normalized = self._normalize_text_for_match(by_area[0].text)
            if self._is_disallowed_brand_candidate(largest_normalized, disallowed_normalized):
                eligible_alternates = [
                    line
                    for line in by_area[1:]
                    if not self._is_disallowed_brand_candidate(
                        self._normalize_text_for_match(line.text), disallowed_normalized
                    )
                ]
                if eligible_alternates:
                    best_alternate = max(
                        eligible_alternates,
                        key=lambda line: self._brand_visual_prominence(
                            line,
                            max_area=max_area,
                            max_height=max_height,
                        ),
                    )
                    return best_alternate.text.strip(), True

        for index, line in enumerate(brand_lines):
            candidate = line.text.strip()
            if len(candidate) < 2:
                continue

            normalized_candidate = self._normalize_text_for_match(candidate)
            if self._is_disallowed_brand_candidate(normalized_candidate, disallowed_normalized):
                continue

            geometry_score = 0.0
            if max_area > 0:
                geometry_score += (line.polygon_area / max_area) * 20
            geometry_score += (line.box_height / max(max_height, 1.0)) * 28
            top_proximity = 1.0 - ((line.min_y - min_top) / vertical_span)
            geometry_score += max(0.0, min(1.0, top_proximity)) * 8
            if line.box_width > 0:
                aspect_ratio = line.box_height / line.box_width
                if aspect_ratio < 0.11:
                    geometry_score -= 10

            score = (
                self._brand_line_score(candidate, index=index, total_lines=total_lines)
                + geometry_score
            )
            if score > best_score:
                best_score = score
                best_candidate = candidate

        if best_candidate is not None:
            return best_candidate, False
        raise VisionExtractorError("Unable to extract brand name")

    def _augment_stacked_brand_candidates(
        self, lines: list[OcrLineCandidate]
    ) -> list[OcrLineCandidate]:
        if len(lines) < 2:
            return lines

        augmented = list(lines)
        ordered = sorted(lines, key=lambda line: (line.min_y, line.center_x))

        for upper, lower in zip(ordered, ordered[1:], strict=False):
            average_height = (upper.box_height + lower.box_height) / 2
            if average_height <= 0:
                continue

            height_delta = abs(upper.box_height - lower.box_height)
            if height_delta > max(2.0, average_height * 0.12):
                continue

            upper_bottom = upper.min_y + upper.box_height
            vertical_gap = lower.min_y - upper_bottom
            if vertical_gap < -2.0 or vertical_gap > max(8.0, average_height * 0.45):
                continue

            center_delta = abs(upper.center_x - lower.center_x)
            allowed_center_delta = max(20.0, max(upper.box_width, lower.box_width) * 0.12)
            if center_delta > allowed_center_delta:
                continue

            combined_text = f"{upper.text} {lower.text}".strip()
            augmented.append(
                OcrLineCandidate(
                    text=combined_text,
                    polygon_area=upper.polygon_area + lower.polygon_area,
                    box_width=max(upper.box_width, lower.box_width),
                    box_height=upper.box_height + lower.box_height + max(0.0, vertical_gap),
                    min_y=min(upper.min_y, lower.min_y),
                    center_x=(upper.center_x + lower.center_x) / 2,
                    center_y=(upper.center_y + lower.center_y) / 2,
                )
            )

        return augmented

    def _normalize_text_for_match(self, value: str) -> str:
        return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()

    def _is_disallowed_brand_candidate(
        self, normalized_candidate: str, disallowed_normalized: set[str]
    ) -> bool:
        return (
            normalized_candidate in disallowed_normalized
            or normalized_candidate in ALCOHOL_CLASS_NORMALIZED
        )

    def _brand_visual_prominence(
        self,
        line: OcrLineCandidate,
        *,
        max_area: float,
        max_height: float,
    ) -> float:
        area_component = (line.polygon_area / max(max_area, 1.0)) * 0.35
        height_component = (line.box_height / max(max_height, 1.0)) * 0.65

        aspect_penalty = 0.0
        if line.box_width > 0:
            aspect_ratio = line.box_height / line.box_width
            if aspect_ratio < 0.11:
                aspect_penalty = 0.18

        return area_component + height_component - aspect_penalty

    def _brand_line_score(self, candidate: str, *, index: int, total_lines: int) -> float:
        normalized = candidate.lower()

        penalty_keywords = (
            "established",
            "since",
            "founded",
            "product of",
            "imported",
            "government warning",
            "proof",
            "alc",
            "vol",
            "ml",
            "fl oz",
            "distilled",
            "bottled",
            "machinery",
            "pregnancy",
            "health problems",
        )
        beverage_keywords = ("whiskey", "bourbon", "vodka", "gin", "rum", "tequila", "wine")

        score = 0.0
        alpha_count = sum(1 for char in candidate if char.isalpha())
        word_count = len(re.findall(r"[A-Za-z]+", candidate))
        digit_count = sum(1 for char in candidate if char.isdigit())
        punctuation_count = sum(1 for char in candidate if char in ",.;:()")
        uppercase_ratio = 0.0
        if alpha_count > 0:
            uppercase_ratio = sum(1 for char in candidate if char.isupper()) / alpha_count

        score += min(alpha_count, 12)
        score += min(word_count * 2, 8)
        score += uppercase_ratio * 8
        score += max(0.0, 7 - (index * 1.4))

        if 2 <= word_count <= 3:
            score += 3
        if word_count >= 5:
            score -= 10
        if alpha_count >= 22:
            score -= 8

        if any(keyword in normalized for keyword in penalty_keywords):
            score -= 14
        if any(keyword in normalized for keyword in beverage_keywords):
            score -= 8
        if re.fullmatch(r"(?:established|since|founded)\s+\d{3,4}", normalized):
            score -= 25

        score -= digit_count * 1.5
        score -= punctuation_count * 2.5
        if candidate and candidate[0].islower():
            score -= 8
        if total_lines > 0 and index > (total_lines // 2):
            score -= 4
        return score

    def _extract_polygon_points(self, polygon: object | None) -> list[tuple[float, float]]:
        if polygon is None:
            return []

        points: list[tuple[float, float]] = []
        if isinstance(polygon, list):
            for point in polygon:
                x = getattr(point, "x", None)
                y = getattr(point, "y", None)
                if isinstance(x, int | float) and isinstance(y, int | float):
                    points.append((float(x), float(y)))
        return points

    def _polygon_area(self, points: list[tuple[float, float]]) -> float:
        if not points:
            return 0.0

        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        return max(0.0, (max_x - min_x) * (max_y - min_y))

    def _box_width(self, points: list[tuple[float, float]]) -> float:
        if not points:
            return 0.0

        min_x = min(point[0] for point in points)
        max_x = max(point[0] for point in points)
        return max(0.0, max_x - min_x)

    def _box_height(self, points: list[tuple[float, float]]) -> float:
        if not points:
            return 0.0

        min_y = min(point[1] for point in points)
        max_y = max(point[1] for point in points)
        return max(0.0, max_y - min_y)

    def _min_y(self, points: list[tuple[float, float]]) -> float:
        if not points:
            return 0.0
        return min(point[1] for point in points)

    def _center_x(self, points: list[tuple[float, float]]) -> float:
        if not points:
            return 0.0
        return sum(point[0] for point in points) / len(points)

    def _center_y(self, points: list[tuple[float, float]]) -> float:
        if not points:
            return 0.0
        return sum(point[1] for point in points) / len(points)

    def _extract_class_type(self, lines: list[str]) -> str | None:
        best_match: tuple[str, int] | None = None

        for line in lines:
            normalized = re.sub(r"\s+", " ", line.lower()).strip()
            score = self._class_line_score(normalized)
            if score <= 0:
                continue

            if best_match is None or score > best_match[1]:
                best_match = (line.strip(), score)

        if best_match is None:
            return None
        return best_match[0]

    def _class_line_score(self, normalized_line: str) -> int:
        score = 0

        for keywords in ALCOHOL_CLASS_KEYWORDS.values():
            for keyword in keywords:
                if keyword in normalized_line:
                    score += max(1, len(keyword.split()))

        if score == 0:
            return 0

        if re.search(r"\b(alc|alc\.?/vol|proof|abv)\b", normalized_line):
            score += 1

        return score

    def _extract_alcohol_percentage(self, raw_text: str) -> float | None:
        # 1. Percentage explicitly anchored to an alcohol keyword (most reliable).
        anchored = re.search(
            r"(\d{1,2}(?:\.\d+)?)\s*%\s*(?:alc|abv|alcohol|by\s+vol)",
            raw_text,
            flags=re.IGNORECASE,
        )
        if anchored is not None:
            return self._coerce_percentage(anchored.group(1))

        anchored_prefix = re.search(
            r"(?:alc(?:ohol)?\.?(?:\s*/?\s*vol\.?)?|abv)[^\d%]{0,15}(\d{1,2}(?:\.\d+)?)\s*%",
            raw_text,
            flags=re.IGNORECASE,
        )
        if anchored_prefix is not None:
            return self._coerce_percentage(anchored_prefix.group(1))

        # 2. Proof statement (e.g. "90 Proof" -> 45% ABV).
        proof = re.search(r"(\d{1,3}(?:\.\d+)?)\s*proof", raw_text, flags=re.IGNORECASE)
        if proof is not None:
            proof_value = self._coerce_percentage(proof.group(1), maximum=200.0)
            if proof_value is not None:
                return round(proof_value / 2, 2)

        # 3. Standalone percentage, guarded against non-ABV uses like "100% agave"
        #    or "2% sulfites". The lookbehind prevents matching "00%" inside "100%".
        for match in re.finditer(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*%", raw_text):
            value = self._coerce_percentage(match.group(1))
            if value is None or value <= 0:
                continue
            context = raw_text[max(0, match.start() - 14) : match.end() + 14].lower()
            if any(term in context for term in ("agave", "juice", "sulfite", "organic")):
                continue
            return value

        return None

    def _coerce_percentage(self, value: str, maximum: float = 100.0) -> float | None:
        try:
            parsed = float(value)
        except ValueError:
            return None
        if parsed < 0 or parsed > maximum:
            return None
        return parsed

    def _extract_net_contents(self, raw_text: str) -> str | None:
        match = re.search(
            r"\b(\d+(?:\.\d+)?)\s*(fl\.?\s*oz|m\s*l|c\s*l|l|oz)\b",
            raw_text,
            flags=re.IGNORECASE,
        )
        if match is None:
            return None

        numeric_value = match.group(1)
        normalized_unit = re.sub(r"[^a-z]", "", match.group(2).lower())
        unit_map = {
            "ml": "mL",
            "cl": "cL",
            "l": "L",
            "oz": "oz",
            "floz": "fl oz",
        }
        canonical_unit = unit_map.get(normalized_unit)
        if canonical_unit is None:
            return None

        return f"{numeric_value} {canonical_unit}"

    def _extract_origin_country(self, raw_text: str) -> str | None:
        patterns = [
            r"^PRODUCT OF\s+([A-Za-z\s]+)$",
            r"^IMPORTED FROM\s+([A-Za-z\s]+)$",
            r"^COUNTRY OF ORIGIN[:\s]+([A-Za-z\s]+)$",
        ]

        for line in raw_text.splitlines():
            candidate = line.strip().upper()
            for pattern in patterns:
                match = re.search(pattern, candidate)
                if match is not None:
                    return match.group(1).strip().title()

        deterministic_country, deterministic_confidence = self._infer_country_from_locations(
            raw_text
        )
        if deterministic_country is not None and deterministic_confidence >= 0.75:
            return deterministic_country

        if self._country_resolver is not None:
            try:
                ai_country, ai_confidence = self._country_resolver.infer_country_from_location_text(
                    raw_text
                )
            except VisionExtractorError:
                logger.warning("country_ai_fallback_failed")
            else:
                if ai_country is not None and ai_confidence >= 0.55:
                    return ai_country

        if deterministic_country is not None:
            return deterministic_country
        return None

    def _infer_country_from_locations(self, raw_text: str) -> tuple[str | None, float]:
        location_patterns: list[tuple[str, str, float]] = [
            (
                r"\b([A-Za-z .'-]+),\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|"
                r"KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|"
                r"RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b",
                "United States",
                0.9,
            ),
            (r"\b([A-Za-z .'-]+),\s*(AB|BC|MB|NB|NL|NS|NT|NU|ON|PE|QC|SK|YT)\b", "Canada", 0.9),
        ]

        keyword_country_map: list[tuple[str, str, float]] = [
            (r"\b(california|new york|texas|kentucky|tennessee|virginia)\b", "United States", 0.85),
            (r"\b(american|usa|u\.s\.a\.|u\.s\.)\b", "United States", 0.76),
            (r"\b(ontario|quebec|british columbia|alberta)\b", "Canada", 0.85),
            (r"\b(scotland|england|wales|northern ireland|london)\b", "United Kingdom", 0.8),
            (r"\b(bordeaux|burgundy|champagne|provence)\b", "France", 0.78),
            (r"\b(tuscany|piedmont|sicily)\b", "Italy", 0.78),
            (r"\b(rioja|catalonia|andalusia)\b", "Spain", 0.78),
            (r"\b(napa|sonoma)\b", "United States", 0.78),
        ]

        best_country: str | None = None
        best_confidence = 0.0
        for line in raw_text.splitlines():
            normalized = line.strip()
            if not normalized:
                continue

            upper_line = normalized.upper()
            for pattern, country, confidence in location_patterns:
                if re.search(pattern, upper_line):
                    if confidence > best_confidence:
                        best_country = country
                        best_confidence = confidence

            lower_line = normalized.lower()
            for pattern, country, confidence in keyword_country_map:
                if re.search(pattern, lower_line):
                    if confidence > best_confidence:
                        best_country = country
                        best_confidence = confidence

        return best_country, best_confidence

    def _extract_government_warning_text(self, raw_text: str) -> str | None:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        start_index = next(
            (index for index, line in enumerate(lines) if "government warning" in line.lower()),
            -1,
        )
        if start_index < 0:
            return None
        return "\n".join(lines[start_index:]).strip()

    def _extract_government_warning_metadata(
        self,
        lines: list[OcrLineCandidate],
    ) -> tuple[bool, str | None, bool | None, float | None]:
        if not lines:
            return False, None, None, None

        warning_start_index = next(
            (
                index
                for index, line in enumerate(lines)
                if "government warning" in line.text.lower()
            ),
            -1,
        )
        if warning_start_index < 0:
            return False, None, None, None

        warning_lines = lines[warning_start_index:]
        warning_text = "\n".join(line.text for line in warning_lines).strip()
        warning_is_all_uppercase = self._is_warning_header_uppercase(warning_text)

        average_line_height = sum(line.box_height for line in lines) / len(lines)
        warning_average_height = sum(line.box_height for line in warning_lines) / len(warning_lines)
        warning_font_size_ratio = None
        if average_line_height > 0:
            warning_font_size_ratio = warning_average_height / average_line_height

        return True, warning_text, warning_is_all_uppercase, warning_font_size_ratio

    def _is_warning_header_uppercase(self, value: str) -> bool:
        if not re.search(r"government\s+warning", value, flags=re.IGNORECASE):
            return False
        return "GOVERNMENT WARNING" in value
