# This file is not used anymore
# parsers/form_1099nec_parser.py

import io
import re
import pdfplumber


def parse_1099_nec(file_bytes: bytes, full_text: str | None = None) -> dict:
    """
    Naive 1099-NEC parser; grabs nonemployee compensation (Box 1).
    """
    if full_text is None:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            texts = [page.extract_text() or "" for page in pdf.pages]
        full_text = "\n".join(texts)

    text = full_text.replace(",", "")
    nonemployee_comp = 0.0

    # Example: "1 Nonemployee compensation 1234.56"
    match = re.search(
        r"1\s+Nonemployee compensation\s+([\d]+(\.\d{1,2})?)",
        text,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(
            r"Nonemployee compensation.*?([\d]+(\.\d{1,2})?)",
            text,
            re.IGNORECASE,
        )

    if match:
        try:
            nonemployee_comp = float(match.group(1))
        except ValueError:
            nonemployee_comp = 0.0

    return {
        "document_type": "1099-NEC",
        "wages": 0.0,
        "federal_withholding": 0.0, 
        "interest_income": 0.0,
        "nonemployee_income": nonemployee_comp,
    }
