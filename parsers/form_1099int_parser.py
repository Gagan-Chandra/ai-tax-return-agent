# This file is not used anymore
# parsers/form_1099int_parser.py

import io
import re
import pdfplumber


def parse_1099_int(file_bytes: bytes, full_text: str | None = None) -> dict:
    """
    Naive 1099-INT parser; grabs interest income (Box 1).
    """
    if full_text is None:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            texts = [page.extract_text() or "" for page in pdf.pages]
        full_text = "\n".join(texts)

    text = full_text.replace(",", "")
    interest_income = 0.0
    match = re.search(r"1\s+Interest income\s+([\d]+(\.\d{1,2})?)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"Interest income.*?([\d]+(\.\d{1,2})?)", text, re.IGNORECASE)

    if match:
        try:
            interest_income = float(match.group(1))
        except ValueError:
            interest_income = 0.0

    return {
        "document_type": "1099-INT",
        "wages": 0.0,
        "federal_withholding": 0.0, 
        "interest_income": interest_income,
        "nonemployee_income": 0.0,
    }
