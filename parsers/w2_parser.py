# This file is not used anymore
# parsers/w2_parser.py

import io
import re
import pdfplumber


def _ensure_full_text(file_bytes: bytes, full_text: str | None) -> str:
    """
    Utility: if full_text is provided use it, otherwise extract from PDF.
    """
    if full_text is not None and full_text.strip():
        return full_text

    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        texts = [page.extract_text() or "" for page in pdf.pages]
    return "\n".join(texts)


def parse_w2(file_bytes: bytes, full_text: str | None = None) -> dict:
    """
    W-2 parser with two stages:
      1) Try to find wages and federal withholding using label-based regex.
      2) If that fails, fall back to a numeric heuristic:
         - largest amount on the page -> wages
         - second largest -> federal withholding (approx)
    """
    text = _ensure_full_text(file_bytes, full_text)

    # Normalize: remove commas, collapse whitespace
    cleaned = text.replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned)

    wages = 0.0
    federal_withholding = 0.0

    # -----------------------
    # 1) Label-based patterns
    # -----------------------

    wages_patterns = [
        r"box\s*1\s*wages.*?(?P<amt>\d+\.\d{1,2}|\d+)",
        r"wages[, ]+tips[, ]+other compensation\s*(?P<amt>\d+\.\d{1,2}|\d+)",
        r"wages\s*(?P<amt>\d+\.\d{1,2}|\d+)",
    ]

    for pattern in wages_patterns:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            try:
                wages = float(m.group("amt"))
                break
            except ValueError:
                continue

    fed_patterns = [
        r"box\s*2\s*federal income tax withheld\s*(?P<amt>\d+\.\d{1,2}|\d+)",
        r"federal income tax withheld\s*(?P<amt>\d+\.\d{1,2}|\d+)",
        r"fed(?:eral)?\s+tax\s+withheld\s*(?P<amt>\d+\.\d{1,2}|\d+)",
    ]

    for pattern in fed_patterns:
        m = re.search(pattern, cleaned, re.IGNORECASE)
        if m:
            try:
                federal_withholding = float(m.group("amt"))
                break
            except ValueError:
                continue

    # ----------------------------------------
    # 2) Numeric fallback heuristic (important)
    # ----------------------------------------
    if wages == 0.0 or federal_withholding == 0.0:
        # Find all numbers with at least 3 digits (to avoid dates, zip codes, etc.)
        num_matches = re.findall(r"\d{3,6}(?:\.\d{1,2})?", cleaned)
        nums = []
        for n in num_matches:
            try:
                nums.append(float(n))
            except ValueError:
                continue

        # Deduplicate & sort desc
        nums = sorted(set(nums), reverse=True)

        if nums:
            if wages == 0.0:
                # Assume largest value is wages
                wages = nums[0]
            if federal_withholding == 0.0 and len(nums) > 1:
                # Take second largest as federal withholding
                federal_withholding = nums[1]

    return {
        "document_type": "W-2",
        "wages": wages,
        "federal_withholding": federal_withholding,
        "interest_income": 0.0,
        "nonemployee_income": 0.0,
    }
