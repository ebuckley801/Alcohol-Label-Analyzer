import re

from app.schemas import ExtractedLabelFields, LabelComplianceResult

REQUIRED_GOV_WARNING = (
    "GOVERNMENT WARNING: (1) ACCORDING TO THE SURGEON GENERAL, WOMEN SHOULD NOT DRINK "
    "ALCOHOLIC BEVERAGES DURING PREGNANCY BECAUSE OF THE RISK OF BIRTH DEFECTS. "
    "(2) CONSUMPTION OF ALCOHOLIC BEVERAGES IMPAIRS YOUR ABILITY TO DRIVE A CAR OR OPERATE "
    "MACHINERY, AND MAY CAUSE HEALTH PROBLEMS."
)


class ComplianceService:
    def evaluate(
        self,
        extracted: ExtractedLabelFields,
        expected_brand_name: str | None = None,
        expected_alcohol_percentage: float | None = None,
        expected_origin_country: str | None = None,
    ) -> LabelComplianceResult:
        issues: list[str] = []

        if self._has_low_quality_ocr_signal(extracted):
            issues.append(
                "Image quality may be poor (angle/lighting), OCR confidence appears low. "
                "Review manually."
            )

        if expected_brand_name and not self._is_same_brand(
            extracted.brand_name,
            expected_brand_name,
        ):
            issues.append("Brand name does not match the submitted application.")

        if (
            expected_alcohol_percentage is not None
            and extracted.alcohol_percentage is not None
            and abs(extracted.alcohol_percentage - expected_alcohol_percentage) > 0.1
        ):
            issues.append("Alcohol percentage does not match the submitted application.")

        if expected_origin_country and extracted.origin_country:
            if extracted.origin_country.strip().lower() != expected_origin_country.strip().lower():
                issues.append("Origin country does not match the submitted application.")

        if not extracted.has_government_warning:
            issues.append("Government warning statement is missing.")
        else:
            warning_is_all_uppercase = extracted.government_warning_is_all_uppercase
            if warning_is_all_uppercase is None:
                warning_is_all_uppercase = self._warning_is_all_uppercase(
                    extracted.government_warning_text
                )

            if warning_is_all_uppercase is False:
                issues.append("Government warning statement must be all uppercase.")

            if (
                extracted.government_warning_font_size_ratio is not None
                and extracted.government_warning_font_size_ratio < 0.3
            ):
                ratio_percent = extracted.government_warning_font_size_ratio * 100
                issues.append(
                    "Government warning text is too small relative to label text "
                    f"({ratio_percent:.1f}% of average font size; minimum is 30.0%)."
                )

            if not self._warning_is_exact(extracted.government_warning_text):
                issues.append("Government warning statement is not exact required wording.")

        return LabelComplianceResult(is_compliant=not issues, issues=issues)

    def _is_same_brand(self, extracted_brand: str, expected_brand: str) -> bool:
        normalized_extracted = re.sub(r"[^a-z0-9]", "", extracted_brand.lower())
        normalized_expected = re.sub(r"[^a-z0-9]", "", expected_brand.lower())
        return normalized_extracted == normalized_expected

    def _warning_is_exact(self, warning_text: str | None) -> bool:
        if warning_text is None:
            return False

        def collapse_space(value: str) -> str:
            return re.sub(r"\s+", " ", value).strip()

        return collapse_space(warning_text) == collapse_space(REQUIRED_GOV_WARNING)

    def _has_low_quality_ocr_signal(self, extracted: ExtractedLabelFields) -> bool:
        raw_text = extracted.raw_text
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

        return has_fragmented_text or has_numeric_noise or has_compact_mixed_noise

    def _warning_is_all_uppercase(self, warning_text: str | None) -> bool:
        if warning_text is None:
            return False

        letters = [char for char in warning_text if char.isalpha()]
        if not letters:
            return False

        return all(char.isupper() for char in letters)
