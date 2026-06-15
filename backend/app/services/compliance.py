import re
import unicodedata
from collections.abc import Callable
from typing import Literal

from app.schemas import ComplianceIssue, ExtractedLabelFields, LabelComplianceResult

# Mandated U.S. health warning statement (27 CFR 16.21). The exact wording is
# required; only the "GOVERNMENT WARNING:" header must additionally be in caps.
GOVERNMENT_WARNING_TEXT = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not "
    "drink alcoholic beverages during pregnancy because of the risk of birth "
    "defects. (2) Consumption of alcoholic beverages impairs your ability to drive "
    "a car or operate machinery, and may cause health problems."
)


def _normalize_for_comparison(value: str) -> str:
    """Lowercase + strip punctuation/whitespace so OCR spacing/punctuation noise
    does not cause false mismatches, while genuine wording changes still differ."""
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


_MANDATED_WARNING_NORMALIZED = _normalize_for_comparison(GOVERNMENT_WARNING_TEXT)

Commodity = Literal["beer", "wine", "spirits", "unknown"]

# Callback used by per-rule helpers to append a structured issue.
AddIssue = Callable[..., None]

# Recognized class/type vocabulary, used both to classify the commodity and to
# flag designations TTB would not recognize (27 CFR 4.34 / 5.63 / 7.121-7.123).
WINE_TERMS = (
    "wine",
    "champagne",
    "prosecco",
    "cava",
    "port",
    "sherry",
    "madeira",
    "vermouth",
    "riesling",
    "chardonnay",
    "cabernet",
    "merlot",
    "pinot",
    "rose",
    "rosé",
    "sauvignon",
    "zinfandel",
    "syrah",
    "shiraz",
    "malbec",
    "sangria",
    "moscato",
    "sparkling",
)
BEER_TERMS = (
    "beer",
    "ale",
    "ipa",
    "lager",
    "stout",
    "porter",
    "pilsner",
    "malt",
    "saison",
    "bock",
    "hefeweizen",
    "kolsch",
    "gose",
    "wheat",
)
SPIRIT_TERMS = (
    "whiskey",
    "whisky",
    "bourbon",
    "rye",
    "scotch",
    "vodka",
    "gin",
    "rum",
    "tequila",
    "mezcal",
    "brandy",
    "cognac",
    "liqueur",
    "spirit",
    "absinthe",
    "schnapps",
)

# Authorized standards of fill in millilitres (27 CFR 5.47 spirits, 4.72 wine).
# Malt beverages have no federal standard of fill, so they are not constrained.
STANDARD_OF_FILL_ML: dict[Commodity, frozenset[float]] = {
    "spirits": frozenset({50, 100, 200, 375, 500, 700, 750, 900, 1000, 1750, 1800, 2000}),
    "wine": frozenset({50, 100, 187, 200, 250, 355, 375, 500, 750, 1000, 1500, 3000}),
}

# Plausible stated-ABV ranges per commodity (% alc/vol). Values outside these are
# almost always an OCR error or a wrong commodity and warrant manual review.
PLAUSIBLE_ABV_RANGE: dict[Commodity, tuple[float, float]] = {
    "beer": (0.0, 25.0),
    "wine": (5.0, 24.0),
    "spirits": (15.0, 80.0),
}

# Phrases that identify the responsible party (bottler/producer/importer) whose
# name and address are mandatory (27 CFR 5.66 / 4.35 / 7.122).
RESPONSIBLE_PARTY_PHRASES = (
    "bottled by",
    "produced by",
    "produced and bottled by",
    "distilled by",
    "distilled and bottled by",
    "imported by",
    "brewed by",
    "brewed and bottled by",
    "vinted by",
    "cellared by",
    "blended by",
    "packed by",
    "manufactured by",
    "made by",
    "bottled for",
)


class ComplianceService:
    def evaluate(
        self,
        extracted: ExtractedLabelFields,
        expected_brand_name: str | None = None,
        expected_alcohol_percentage: float | None = None,
        expected_origin_country: str | None = None,
    ) -> LabelComplianceResult:
        issues: list[ComplianceIssue] = []

        def add(code: str, message: str, severity: str, citation: str | None = None) -> None:
            issues.append(
                ComplianceIssue(
                    code=code,
                    message=message,
                    severity=severity,  # type: ignore[arg-type]
                    citation=citation,
                )
            )

        commodity = self._classify_commodity(extracted.class_type)

        if self._has_low_quality_ocr_signal(extracted):
            add(
                "ocr.low_quality",
                "Image quality may be poor (angle/lighting), OCR confidence appears low. "
                "Review manually.",
                "warning",
            )

        # --- Mandatory fields (presence requirements vary by commodity) ---
        if not extracted.brand_name.strip():
            add(
                "field.brand_missing",
                "Brand name is missing.",
                "error",
                "27 CFR 4.33 / 5.63 / 7.122",
            )

        if extracted.class_type is None or not extracted.class_type.strip():
            add(
                "field.class_missing",
                "Class/type designation is missing.",
                "error",
                "27 CFR 4.34 / 5.63 / 7.121",
            )
        elif commodity == "unknown":
            add(
                "class.unrecognized",
                f"Class/type '{extracted.class_type.strip()}' is not a recognized TTB "
                "designation; verify it maps to an approved class/type.",
                "warning",
                "27 CFR 4.34 / 5.63 / 7.121",
            )

        # ABV is mandatory on wine and distilled spirits; on malt beverages it is
        # only required in limited cases, so do not hard-fail beer for a missing ABV.
        if extracted.alcohol_percentage is None and commodity in ("wine", "spirits"):
            add(
                "field.abv_missing",
                "Alcohol content is missing.",
                "error",
                "27 CFR 4.36 / 5.65",
            )
        elif extracted.alcohol_percentage is None and commodity in ("beer", "unknown"):
            add(
                "field.abv_absent",
                "Alcohol content is not stated (acceptable for many malt beverages; "
                "confirm it is not required for this product).",
                "info",
                "27 CFR 7.65",
            )

        if extracted.net_contents is None or not extracted.net_contents.strip():
            add(
                "field.net_missing",
                "Net contents are missing.",
                "error",
                "27 CFR 4.37 / 5.70 / 7.70",
            )
        else:
            self._check_standard_of_fill(extracted.net_contents, commodity, add)

        # Responsible-party name and address are mandatory on every label.
        if not self._has_responsible_party(extracted.raw_text):
            add(
                "field.responsible_party",
                "No bottler/producer/importer name-and-address statement (e.g. "
                "'Bottled by …') was detected. Verify it is present and legible.",
                "warning",
                "27 CFR 4.35 / 5.66 / 7.122",
            )

        # Sulfite declaration is mandatory on wine containing 10+ ppm SO2.
        if commodity == "wine" and not self._has_sulfite_declaration(extracted.raw_text):
            add(
                "wine.sulfites",
                "Wine label is missing a sulfite declaration ('CONTAINS SULFITES'). "
                "Required when SO2 is 10 ppm or more.",
                "error",
                "27 CFR 4.32(e)",
            )

        # --- ABV plausibility and proof/ABV consistency ---
        if extracted.alcohol_percentage is not None:
            self._check_abv_plausibility(extracted.alcohol_percentage, commodity, add)
            if commodity == "spirits":
                self._check_proof_consistency(extracted.alcohol_percentage, extracted.raw_text, add)

        # --- Cross-check against the submitted application ---
        if expected_brand_name and not self._is_same_brand(
            extracted.brand_name,
            expected_brand_name,
        ):
            add(
                "match.brand",
                "Brand name does not match the submitted application.",
                "error",
            )

        if expected_alcohol_percentage is not None and extracted.alcohol_percentage is not None:
            tolerance = self._abv_tolerance(commodity, expected_alcohol_percentage)
            if abs(extracted.alcohol_percentage - expected_alcohol_percentage) > tolerance:
                add(
                    "match.abv",
                    "Alcohol percentage does not match the submitted application "
                    f"(tolerance ±{tolerance:g}%).",
                    "error",
                    "27 CFR 4.36 / 5.37 / 7.71",
                )

        if expected_origin_country and extracted.origin_country:
            if extracted.origin_country.strip().lower() != expected_origin_country.strip().lower():
                add(
                    "match.origin",
                    "Origin country does not match the submitted application.",
                    "error",
                )

        # --- Government health warning ---
        if not extracted.has_government_warning:
            add(
                "warning.missing",
                "Government warning statement is missing.",
                "error",
                "27 CFR 16.21",
            )
        else:
            warning_header_is_uppercase = extracted.government_warning_is_all_uppercase
            if warning_header_is_uppercase is None:
                warning_header_is_uppercase = self._warning_header_is_uppercase(
                    extracted.government_warning_text
                )

            if warning_header_is_uppercase is False:
                add(
                    "warning.header_case",
                    "Government warning header must be uppercase: 'GOVERNMENT WARNING'.",
                    "error",
                    "27 CFR 16.22(a)(1)",
                )

            if not self._warning_text_matches_mandated(extracted.government_warning_text):
                add(
                    "warning.text",
                    "Government warning text does not match the mandated TTB statement "
                    "word-for-word.",
                    "error",
                    "27 CFR 16.22(a)(1)",
                )
            elif not self._warning_is_separate(extracted.government_warning_text):
                add(
                    "warning.separateness",
                    "Government warning may not appear as a separate, standalone "
                    "statement; verify no other label text is interspersed within it.",
                    "warning",
                    "27 CFR 16.22(a)",
                )

            if (
                extracted.government_warning_font_size_ratio is not None
                and extracted.government_warning_font_size_ratio < 0.3
            ):
                ratio_percent = extracted.government_warning_font_size_ratio * 100
                add(
                    "warning.type_size",
                    "Government warning text is too small relative to label text "
                    f"({ratio_percent:.1f}% of average font size; minimum is 30.0%).",
                    "error",
                    "27 CFR 16.22(b)",
                )

        has_error = any(issue.severity == "error" for issue in issues)
        return LabelComplianceResult(
            is_compliant=not has_error,
            issues=[issue.message for issue in issues],
            issues_detail=issues,
        )

    def _classify_commodity(self, class_type: str | None) -> Commodity:
        """Map a class/type designation to a broad commodity so downstream rules
        can apply the correct TTB requirements (beer vs. wine vs. spirits)."""
        if class_type is None:
            return "unknown"
        text = class_type.lower()
        # Spirits/wine terms are checked before beer because "malt" appears in
        # beer terms but spirits/wine designations are more specific.
        if any(term in text for term in SPIRIT_TERMS):
            return "spirits"
        if any(term in text for term in WINE_TERMS):
            return "wine"
        if any(term in text for term in BEER_TERMS):
            return "beer"
        return "unknown"

    def _abv_tolerance(self, commodity: Commodity, expected_abv: float) -> float:
        """TTB stated-ABV labeling tolerances, refined by commodity and bracket.

        Wine: ±1.5% at or below 14%, ±1.0% above 14% (27 CFR 4.36(b)).
        Malt beverages: ±0.3% (27 CFR 7.71). Distilled spirits: ±0.15% (27 CFR
        5.37). Unknown commodities fall back to the strictest realistic value.
        """
        if commodity == "wine":
            return 1.5 if expected_abv <= 14.0 else 1.0
        if commodity == "beer":
            return 0.3
        if commodity == "spirits":
            return 0.15
        return 0.3

    def _check_standard_of_fill(
        self,
        net_contents: str,
        commodity: Commodity,
        add: "AddIssue",
    ) -> None:
        allowed = STANDARD_OF_FILL_ML.get(commodity)
        if allowed is None:
            return  # Malt beverages / unknown: no federal standard of fill.
        volume_ml = self._parse_net_contents_ml(net_contents)
        if volume_ml is None:
            return  # Unparseable units (e.g. fl oz); leave for manual review.
        if not any(abs(volume_ml - size) <= 1.0 for size in allowed):
            add(
                "field.standard_of_fill",
                f"Net contents ({net_contents.strip()} ≈ {volume_ml:g} mL) is not an "
                "authorized standard of fill for this commodity.",
                "warning",
                "27 CFR 4.72 / 5.47",
            )

    def _parse_net_contents_ml(self, net_contents: str) -> float | None:
        match = re.search(
            r"(\d+(?:[.,]\d+)?)\s*(ml|millilit(?:er|re)s?|cl|centilit(?:er|re)s?|l|lit(?:er|re)s?)\b",
            net_contents.lower(),
        )
        if match is None:
            return None
        value = float(match.group(1).replace(",", "."))
        unit = match.group(2)
        if unit.startswith("ml") or unit.startswith("milli"):
            return value
        if unit.startswith("cl") or unit.startswith("centi"):
            return value * 10
        return value * 1000  # litres

    def _has_responsible_party(self, raw_text: str | None) -> bool:
        if raw_text is None:
            return False
        text = raw_text.lower()
        return any(phrase in text for phrase in RESPONSIBLE_PARTY_PHRASES)

    def _has_sulfite_declaration(self, raw_text: str | None) -> bool:
        if raw_text is None:
            return False
        normalized = _normalize_for_comparison(raw_text)
        return "contains sulfites" in normalized or "contains sulphites" in normalized

    def _check_abv_plausibility(
        self,
        abv: float,
        commodity: Commodity,
        add: "AddIssue",
    ) -> None:
        bounds = PLAUSIBLE_ABV_RANGE.get(commodity)
        if bounds is None:
            return
        low, high = bounds
        if abv < low or abv > high:
            add(
                "abv.plausibility",
                f"Stated alcohol content ({abv:g}%) is outside the typical range for "
                f"{commodity} ({low:g}-{high:g}%); verify the reading.",
                "warning",
            )

    def _check_proof_consistency(
        self,
        abv: float,
        raw_text: str | None,
        add: "AddIssue",
    ) -> None:
        if raw_text is None:
            return
        match = re.search(r"(\d+(?:\.\d+)?)\s*proof\b", raw_text.lower())
        if match is None:
            return
        proof = float(match.group(1))
        if abs(proof - 2 * abv) > 1.0:
            add(
                "spirits.proof_abv",
                f"Proof ({proof:g}) is inconsistent with stated ABV ({abv:g}%); proof "
                "must equal twice the alcohol-by-volume percentage.",
                "warning",
                "27 CFR 5.65(a)",
            )

    def _warning_is_separate(self, warning_text: str | None) -> bool:
        """Heuristic: the mandated wording should run contiguously. If other text
        is interspersed between the header and the two clauses, the normalized
        mandated string would not appear as a contiguous substring."""
        if warning_text is None:
            return False
        return _MANDATED_WARNING_NORMALIZED in _normalize_for_comparison(warning_text)

    def _is_same_brand(self, extracted_brand: str, expected_brand: str) -> bool:
        return self._normalize_brand(extracted_brand) == self._normalize_brand(expected_brand)

    def _normalize_brand(self, value: str) -> str:
        # Fold accents (Château -> Chateau) before stripping non-alphanumerics so
        # accented and unaccented spellings compare equal.
        decomposed = unicodedata.normalize("NFKD", value)
        without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]", "", without_marks.lower())

    def _warning_text_matches_mandated(self, warning_text: str | None) -> bool:
        if warning_text is None:
            return False
        normalized = _normalize_for_comparison(warning_text)
        # Containment (not equality) tolerates trailing label text that OCR may
        # capture after the warning, while still catching altered/missing wording.
        return _MANDATED_WARNING_NORMALIZED in normalized

    def _warning_header_is_uppercase(self, warning_text: str | None) -> bool:
        if warning_text is None:
            return False
        return "GOVERNMENT WARNING" in warning_text

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
