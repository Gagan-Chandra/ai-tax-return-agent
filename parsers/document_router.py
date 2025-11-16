# parsers/document_router.py

import io
import re
import os
import json
import base64
import logging
from typing import Dict, Any

import pdfplumber
import pytesseract
from PIL import Image

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.NullHandler())
logger.setLevel(logging.INFO)

# =====================================================
# 1. Tesseract / OCR availability
# =====================================================
OCR_AVAILABLE = True

if os.name == "nt":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

try:
    _ = pytesseract.get_tesseract_version()
except (pytesseract.TesseractNotFoundError, OSError):
    OCR_AVAILABLE = False
    logger.warning(
        "Tesseract OCR is not available in this environment. "
        "OCR fallback will be disabled, pdfplumber-only extraction will be used."
    )

# =====================================================
# 2. Optional Groq LLM client (TEXT ONLY, no vision)
# =====================================================
try:
    # We reuse the same helper you already use in ai_advisor.py
    from ai_advisor import _get_groq_client  # type: ignore
except Exception:
    _get_groq_client = None


# =====================================================
# 3. Text + OCR extraction
# =====================================================
def _ocr_extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Fallback OCR using Tesseract for scanned PDFs.

    On environments where Tesseract is NOT installed (for example,
    Streamlit Cloud), this will simply log a warning and return an
    empty string so the app does not crash.
    """
    if not OCR_AVAILABLE:
        logger.warning("OCR requested but Tesseract is not available; skipping OCR.")
        return ""

    text_chunks: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages):
                try:
                    # Render page to image
                    page_img = page.to_image(resolution=300).original

                    # Basic pre-processing to help OCR a bit
                    gray = page_img.convert("L")
                    text = pytesseract.image_to_string(gray, lang="eng")
                    text_chunks.append(text)
                except Exception as e:
                    logger.error(
                        "OCR: Error processing page %s: %s",
                        page_idx,
                        e,
                    )
    except Exception as e:
        logger.error("OCR: could not open PDF for OCR: %s", e)

    full_text = "\n".join(text_chunks)
    logger.info("OCR extracted %d characters from scanned PDF", len(full_text))
    return full_text


def extract_text_from_pdf(file_bytes: bytes) -> str:
    """
    Try pdfplumber first. If we get very little or no text, fall back to OCR.
    """
    text_chunks = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                text_chunks.append(page_text)
    except Exception as e:
        logger.error("pdfplumber failed to open PDF: %s", e)

    full_text = "\n".join(text_chunks)
    logger.info("pdfplumber extracted %d characters", len(full_text))

    # If we got basically nothing, try OCR
    if len(full_text.strip()) < 50:
        logger.warning(
            "Very little text extracted via pdfplumber; attempting OCR fallback..."
        )
        full_text = _ocr_extract_text_from_pdf(file_bytes)

    if not full_text.strip():
        logger.warning(
            "No text extracted at all from this PDF (even after OCR)."
        )

    return full_text


def detect_document_type(full_text: str) -> str:
    """
    Detect whether the PDF is a W-2, 1099-INT, or 1099-NEC.
    We use simple keyword checks tuned to the sample forms.
    """
    t = full_text.lower()

    if "1099-nec" in t or "nonemployee compensation" in t or "upwork global" in t:
        return "1099-NEC"

    if "1099-int" in t or "interest income" in t or "xyz bank" in t:
        return "1099-INT"

    if "w-2" in t or "w2" in t or "wage and tax statement" in t:
        return "W-2"

    # Defaulting to W-2 as you had before
    return "W-2"


_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")
_DOLLAR_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)")


def _find_labeled_number(
    full_text: str,
    label_regex: str,
    *,
    min_value: float | None = None,
    max_value: float | None = None,
) -> float | None:
    """
    Find the FIRST numeric value that appears shortly after a label,
    with optional min/max filters and skipping EIN/SSN-style tokens.

    We search within ~200 characters after the label.
    """
    pattern = re.compile(label_regex, re.IGNORECASE | re.DOTALL)
    label_match = pattern.search(full_text)
    if not label_match:
        return None

    base_idx = label_match.end()
    window = full_text[base_idx : base_idx + 200]

    for m in _NUM_RE.finditer(window):
        num_str = m.group()

        abs_start = base_idx + m.start()
        abs_end = base_idx + m.end()

        before = full_text[abs_start - 1] if abs_start > 0 else ""
        after = full_text[abs_end] if abs_end < len(full_text) else ""

        looks_like_ein_ssn = False

        if after == "-" and abs_end + 1 < len(full_text) and full_text[abs_end + 1].isdigit():
            looks_like_ein_ssn = True

        if before == "-" and abs_start - 2 >= 0 and full_text[abs_start - 2].isdigit():
            looks_like_ein_ssn = True

        if looks_like_ein_ssn:
            continue

        try:
            val = float(num_str.replace(",", ""))
        except ValueError:
            continue

        if min_value is not None and val < min_value:
            continue
        if max_value is not None and val > max_value:
            continue

        return val

    return None


# =====================================================
# 4. W-2 parsing (simple layout + general)
# =====================================================
def _parse_w2_simple_layout(full_text: str) -> Dict[str, float] | None:
    """
    Special-case parser for your minimal W-2 layout, e.g.:

        123-45-6789
        54000.00 5200.00
        54000.00 3348.00
        Sample Corp INC.
        54000.00 783.00
        Jhon Doe

    Pattern:
      - First non-empty line: SSN (NNN-NN-NNNN)
      - Next non-empty line with >= 2 numeric values and NO letters:
            wages, federal_withholding

    This MUST NOT run for real W-2 layouts with labels, so we also
    check that the full text does NOT contain typical label keywords.
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    if not lines:
        return None

    lower_text = full_text.lower()
    label_keywords = [
        "wages, tips, other",
        "wage and tax statement",
        "federal income tax withheld",
        "employer's id number",
        "box 1", "box 2",
    ]
    if any(k in lower_text for k in label_keywords):
        return None

    first_idx = 0
    ssn_line = lines[first_idx]
    if not re.fullmatch(r"\d{3}-\d{2}-\d{4}", ssn_line):
        return None

    ssn = ssn_line

    for idx in range(first_idx + 1, len(lines)):
        line = lines[idx]
        if re.search(r"[A-Za-z]", line):
            continue

        nums = re.findall(r"\d+(?:\.\d+)?", line)
        if len(nums) < 2:
            continue

        try:
            wages = float(nums[0])
            fed_with = float(nums[1])
        except ValueError:
            continue

        if wages < 1000 or wages > 10_000_000:
            continue
        if fed_with < 0 or fed_with > wages:
            continue

        logger.info(
            "W-2 simple-layout parse succeeded: SSN=%s, wages=%s, fed_withholding=%s",
            ssn, wages, fed_with,
        )
        return {
            "document_type": "W-2",
            "wages": wages,
            "federal_withholding": fed_with,
            "interest_income": 0.0,
            "nonemployee_income": 0.0,
            "ssn": ssn,
        }

    return None


def parse_w2_from_text(full_text: str) -> Dict[str, float]:
    """
    W-2 parser with two layers:

      1) _parse_w2_simple_layout:
         - For your special compact numeric-only W-2.

      2) General robust parser (original logic):
         - Uses label-based lookup for wages & federal withholding,
           and falls back to numeric heuristics.
    """
    simple = _parse_w2_simple_layout(full_text)
    if simple is not None:
        return simple

    wages = _find_labeled_number(
        full_text,
        r"wages[, ]+tips[, ]+other[^a-z0-9]{0,20}comp(?:ensation)?",
        min_value=1_000,
        max_value=5_000_000,
    )

    try:
        nums = [float(x.replace(",", "")) for x in _NUM_RE.findall(full_text)]
    except Exception as e:
        logger.error("Error parsing numeric values from W-2 text: %s", e)
        nums = []

    if wages is None or wages <= 0 or wages > 5_000_000:
        wages = 0.0
        if nums:
            freq: Dict[float, int] = {}
            for n in nums:
                if 1_000 <= n <= 5_000_000:
                    freq[n] = freq.get(n, 0) + 1
            if freq:
                wages = max(freq.items(), key=lambda kv: (kv[1], kv[0]))[0]

    max_withholding = wages * 0.4 if wages and wages > 0 else None

    fed_withholding = _find_labeled_number(
        full_text,
        r"federal income tax wit\w*",
        min_value=10,
        max_value=max_withholding or 1_000_000,
    )

    logger.info(
        "W-2 label-based parse (general): wages=%s, fed_withholding=%s",
        wages,
        fed_withholding,
    )

    if fed_withholding is None or fed_withholding <= 0:
        fed_withholding = 0.0
        if nums:
            candidates = []
            if wages and wages > 0:
                for n in nums:
                    if 10 <= n <= wages * 0.4:
                        candidates.append(n)

            if not candidates and wages and wages > 0:
                for n in nums:
                    if 10 <= n <= wages and abs(n - wages) > 1e-6:
                        candidates.append(n)

            if candidates:
                fed_withholding = max(candidates)

    logger.info(
        "W-2 final general parse: wages=%s, federal_withholding=%s",
        wages,
        fed_withholding,
    )

    return {
        "document_type": "W-2",
        "wages": float(wages or 0.0),
        "federal_withholding": float(fed_withholding or 0.0),
        "interest_income": 0.0,
        "nonemployee_income": 0.0,
        "ssn": "",
    }


# =====================================================
# 5. 1099-INT parsing
# =====================================================
def _parse_1099_int_core(full_text: str) -> Dict[str, float]:
    """
    Legacy EIN-based parsing for 1099-INT.
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    ein_ssn_idx = None

    for idx, line in enumerate(lines):
        if re.search(r"\d{2}-\d{7}\s+\d{3}-\d{2}-\d{4}", line):
            ein_ssn_idx = idx
            break

    interest = 0.0
    fed_with = 0.0

    if ein_ssn_idx is not None:
        for j in range(ein_ssn_idx - 1, -1, -1):
            if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", lines[j]):
                interest = float(lines[j].replace(",", ""))
                break

        for j in range(ein_ssn_idx + 1, len(lines)):
            if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", lines[j]):
                fed_with = float(lines[j].replace(",", ""))
                break

    return {"interest_income": interest, "federal_withholding": fed_with}


def parse_1099_int_from_text(full_text: str) -> Dict[str, float]:
    """
    1099-INT parser tuned for your forms and noisy OCR.
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    lower_lines = [ln.lower() for ln in lines]
    box_values: Dict[int, float] = {}
    current_box: int | None = None

    for idx, raw in enumerate(lines):
        m_box = re.match(r"^\s*(\d{1,2})\b", raw)
        if m_box:
            current_box = int(m_box.group(1))
            logger.info("1099-INT: line %d starts box %s -> %s", idx, current_box, raw)

        if current_box is not None and "$" in raw and current_box not in box_values:
            m_dollar = _DOLLAR_RE.search(raw)
            if m_dollar:
                try:
                    val = float(m_dollar.group(1).replace(",", ""))
                    box_values[current_box] = val
                    logger.info(
                        "1099-INT: captured box %s amount from line %d: %s (line='%s')",
                        current_box,
                        idx,
                        val,
                        raw,
                    )
                except ValueError:
                    logger.warning(
                        "1099-INT: failed to parse dollar amount on line %d: %s",
                        idx,
                        raw,
                    )

    interest = box_values.get(1, 0.0)
    fed_withholding = box_values.get(4, 0.0)

    logger.info(
        "1099-INT: after box scan -> box_values=%s, interest=%s, fed_withholding=%s",
        box_values,
        interest,
        fed_withholding,
    )

    if interest == 0.0:
        label_idx = None
        for i, low in enumerate(lower_lines):
            if "interest income" in low:
                label_idx = i
                break

        if label_idx is not None:
            end_idx = min(label_idx + 8, len(lines))
            region_lines = lines[label_idx:end_idx]
            region_text = "\n".join(region_lines)
            logger.info(
                "1099-INT: interest fallback region lines %d-%d:\n%s",
                label_idx,
                end_idx - 1,
                region_text,
            )

            candidates: list[float] = []
            for num_str in _NUM_RE.findall(region_text):
                try:
                    v = float(num_str.replace(",", ""))
                except ValueError:
                    continue
                if v < 10:
                    continue
                if v > 5_000_000:
                    continue
                candidates.append(v)

            logger.info("1099-INT: interest fallback numeric candidates=%s", candidates)

            if candidates:
                interest = max(candidates)
                logger.info(
                    "1099-INT: interest fallback chose %.2f as Box 1 amount",
                    interest,
                )

    if interest == 0.0 and fed_withholding == 0.0:
        core = _parse_1099_int_core(full_text)
        logger.info("1099-INT: both values zero, core fallback parse=%s", core)
        interest = core["interest_income"] or 0.0
        fed_withholding = core["federal_withholding"] or 0.0

    result = {
        "document_type": "1099-INT",
        "wages": 0.0,
        "interest_income": float(interest),
        "nonemployee_income": 0.0,
        "federal_withholding": float(fed_withholding),
    }
    logger.info("1099-INT final parse: %s", result)
    return result


# =====================================================
# 6. 1099-NEC parsing
# =====================================================
def _parse_1099_nec_core(full_text: str) -> Dict[str, float]:
    """
    Simple positional fallback, same style as _parse_1099_int_core.
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    ein_ssn_idx = None

    for idx, line in enumerate(lines):
        if re.search(r"\d{2}-\d{7}\s+\d{3}-\d{2}-\d{4}", line):
            ein_ssn_idx = idx
            break

    nonemp = 0.0
    fed_with = 0.0

    if ein_ssn_idx is not None:
        for j in range(ein_ssn_idx + 1, len(lines)):
            if re.fullmatch(r"\d+(?:\.\d+)?", lines[j]):
                nonemp = float(lines[j])
                break

        for j in range(ein_ssn_idx + 2, len(lines)):
            if re.fullmatch(r"\d+(?:\.\d+)?", lines[j]):
                fed_with = float(lines[j])
                break

    return {"nonemployee_income": nonemp, "federal_withholding": fed_with}


def parse_1099_nec_from_text(full_text: str) -> Dict[str, float]:
    """
    Robust 1099-NEC parser.
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    lower_lines = [ln.lower() for ln in lines]
    box_values: Dict[int, float] = {}
    current_box: int | None = None

    for idx, raw in enumerate(lines):
        m_box = re.match(r"^\s*(\d{1,2})\b", raw)
        if m_box:
            current_box = int(m_box.group(1))
            logger.info("1099-NEC: line %d starts box %s -> %s", idx, current_box, raw)

        if current_box is not None and "$" in raw and current_box not in box_values:
            raw_low = raw.lower()

            if current_box == 1 and (
                "5,000 or more" in raw_low or "5000 or more" in raw_low
            ):
                logger.info(
                    "1099-NEC: skipping Box 1 candidate from line %d "
                    "because it contains '5,000 or more': %s",
                    idx,
                    raw,
                )
                continue

            m_dollar = _DOLLAR_RE.search(raw)
            if m_dollar:
                try:
                    val = float(m_dollar.group(1).replace(",", ""))
                    box_values[current_box] = val
                    logger.info(
                        "1099-NEC: captured box %s amount from line %d: %s (line='%s')",
                        current_box,
                        idx,
                        val,
                        raw,
                    )
                except ValueError:
                    logger.warning(
                        "1099-NEC: failed to parse dollar amount on line %d: %s",
                        idx,
                        raw,
                    )

    nonemp = box_values.get(1, 0.0)
    fed_withholding = box_values.get(4, 0.0)

    logger.info(
        "1099-NEC: after box scan -> box_values=%s, nonemp=%s, fed_withholding=%s",
        box_values,
        nonemp,
        fed_withholding,
    )

    if nonemp == 0.0:
        label_idx = None
        for i, low in enumerate(lower_lines):
            if ("nonemployee" in low or "non employee" in low) and "compens" in low:
                label_idx = i
                break

        if label_idx is not None:
            end_idx = min(label_idx + 8, len(lines))
            region_text = "\n".join(lines[label_idx:end_idx])
            region_low = region_text.lower()

            logger.info(
                "1099-NEC: nonemployee fallback region %d-%d:\n%s",
                label_idx,
                end_idx - 1,
                region_text,
            )

            candidates: list[float] = []
            for num_str in _NUM_RE.findall(region_text):
                try:
                    v = float(num_str.replace(",", ""))
                except ValueError:
                    continue

                if v < 10 or v > 5_000_000:
                    continue

                if (
                    abs(v - 5000.0) < 1e-6
                    and ("5,000 or more" in region_low or "5000 or more" in region_low)
                ):
                    logger.info(
                        "1099-NEC: skipping 5000.0 candidate from fallback region "
                        "because it's part of the '5,000 or more' phrase."
                    )
                    continue

                candidates.append(v)

            logger.info(
                "1099-NEC: nonemployee fallback numeric candidates=%s",
                candidates,
            )

            if candidates:
                nonemp = max(candidates)
                logger.info(
                    "1099-NEC: nonemployee fallback chose %.2f as Box 1",
                    nonemp,
                )

    if fed_withholding == 0.0:
        label_idx = None
        for i, low in enumerate(lower_lines):
            if (
                "federal" in low
                and "income" in low
                and "tax" in low
                and "with" in low
            ):
                label_idx = i
                break

        if label_idx is not None:
            end_idx = min(label_idx + 6, len(lines))
            logger.info(
                "1099-NEC: federal withholding label at line %d, "
                "searching lines %d-%d",
                label_idx,
                label_idx,
                end_idx - 1,
            )

            candidates: list[float] = []

            for j in range(label_idx, end_idx):
                raw = lines[j]
                raw_low = lower_lines[j]

                if "$" not in raw:
                    continue

                if any(word in raw_low for word in ["state", "nonemployee", "compens"]):
                    logger.info(
                        "1099-NEC: skipping candidate line %d for Box 4 "
                        "because it contains a forbidden word: %s",
                        j,
                        raw,
                    )
                    continue

                m_dollar = _DOLLAR_RE.search(raw)
                if not m_dollar:
                    continue

                try:
                    v = float(m_dollar.group(1).replace(",", ""))
                except ValueError:
                    continue

                if v < 10 or v > 5_000_000:
                    continue

                if nonemp > 0 and v > nonemp * 0.6:
                    logger.info(
                        "1099-NEC: skipping Box 4 candidate %.2f on line %d "
                        "because it exceeds 60%% of nonemp (%.2f)",
                        v,
                        j,
                        nonemp,
                    )
                    continue

                candidates.append(v)
                logger.info(
                    "1099-NEC: Box 4 candidate from line %d: %.2f (line='%s')",
                    j,
                    v,
                    raw,
                )

            logger.info(
                "1099-NEC: federal withholding fallback candidates=%s",
                candidates,
            )

            if candidates:
                fed_withholding = max(candidates)
                logger.info(
                    "1099-NEC: federal withholding fallback chose %.2f as Box 4",
                    fed_withholding,
                )

    if nonemp == 0.0 and fed_withholding == 0.0:
        core = _parse_1099_nec_core(full_text)
        logger.info("1099-NEC: both zero, core fallback parse=%s", core)
        nonemp = core.get("nonemployee_income", 0.0) or 0.0
        fed_withholding = core.get("federal_withholding", 0.0) or 0.0

    result = {
        "document_type": "1099-NEC",
        "wages": 0.0,
        "interest_income": 0.0,
        "nonemployee_income": float(nonemp or 0.0),
        "federal_withholding": float(fed_withholding or 0.0),
    }
    logger.info("1099-NEC final parse: %s", result)
    return result


# =====================================================
# 7. Groq LLM-based parsing FROM TEXT (no vision)
# =====================================================
def _groq_llm_parse_from_text(ocr_text: str) -> Dict[str, Any]:
    """
    Use a Groq TEXT model (no images) to read the OCR text and extract fields.

    Returns:
      {
        "document_type": "...",
        "parsed": {...},      # with wages, interest_income, etc.
        "llm_raw": "<raw LLM response>"
      }
    """
    if _get_groq_client is None:
        raise RuntimeError("Groq helper (_get_groq_client) not available in this environment.")

    client, err = _get_groq_client()
    if err:
        raise RuntimeError(err)

    # Limit text length so we don't blow the context window
    text = ocr_text or ""
    if len(text) > 15000:
        text = text[:15000]

    schema_instruction = (
        "You are reading OCR-extracted U.S. tax forms (W-2, 1099-INT, 1099-NEC).\n"
        "Extract the key values and return ONLY valid JSON with this schema:\n\n"
        "{\n"
        '  \"document_type\": \"W-2\" | \"1099-INT\" | \"1099-NEC\" | \"UNKNOWN\",\n'
        '  \"ssn\": \"123-45-6789\" | null,\n'
        '  \"wages\": number,\n'
        '  \"federal_withholding\": number,\n'
        '  \"interest_income\": number,\n'
        '  \"nonemployee_income\": number\n'
        "}\n\n"
        "Use 0 for any missing amounts. "
        "Use document_type \"UNKNOWN\" if you are not confident. "
        "Do not include any explanation, only the JSON object."
    )

    user_content = schema_instruction + "\n\n--- OCR TEXT START ---\n" + text + "\n--- OCR TEXT END ---"

    chat = client.chat.completions.create(
        model="llama-3.3-70b-versatile",  # TEXT-ONLY model, supported by Groq
        messages=[
            {
                "role": "system",
                "content": "You are a strict JSON extraction engine for U.S. tax forms.",
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        temperature=0,
        max_tokens=512,
    )

    raw_content = chat.choices[0].message.content or ""

    # Try to parse JSON from the LLM response
    try:
        data = json.loads(raw_content)
    except Exception:
        m = re.search(r"\{.*\}", raw_content, re.DOTALL)
        if not m:
            raise RuntimeError(f"Groq LLM did not return JSON: {raw_content[:300]!r}")
        data = json.loads(m.group(0))

    doc_type = (data.get("document_type") or "UNKNOWN").upper()

    parsed = {
        "document_type": doc_type,
        "wages": float(data.get("wages") or 0.0),
        "federal_withholding": float(data.get("federal_withholding") or 0.0),
        "interest_income": float(data.get("interest_income") or 0.0),
        "nonemployee_income": float(data.get("nonemployee_income") or 0.0),
        "ssn": (data.get("ssn") or "").strip(),
    }

    return {
        "document_type": doc_type,
        "parsed": parsed,
        "llm_raw": raw_content,
    }


# =====================================================
# 8. Main entrypoint
# =====================================================
def analyze_document(file_bytes: bytes, use_llm: bool = False) -> Dict[str, Any]:
    """
    Main entrypoint called from app.py.

    If use_llm is False:
        - Use your existing rule-based parsing.
    If use_llm is True:
        - Use Groq TEXT LLM on OCR text to extract fields.
        - If it fails, fall back to rule-based parsing.
    """
    # 1) Always get OCR / extracted text first
    raw_text = extract_text_from_pdf(file_bytes)
    logger.info("First 500 chars of extracted text:\n%s", raw_text[:500])

    # 2) Default doc_type from keyword detection
    doc_type = detect_document_type(raw_text)
    logger.info("Detected document type (keyword-based): %s", doc_type)

    parser_used = "rule-based"
    parsed: Dict[str, Any] | None = None

    # 3) Optional Groq LLM parsing from text
    if use_llm:
        try:
            llm_result = _groq_llm_parse_from_text(raw_text)
            parsed = llm_result["parsed"]
            # Allow the LLM to override document_type if it thinks it's different
            doc_type = llm_result.get("document_type", doc_type)
            parser_used = "Groq LLM (from OCR text)"
        except Exception as e:
            logger.error("Groq LLM parsing failed; falling back to rule-based: %s", e)
            parser_used = f"Groq LLM ERROR (fallback to rules): {e}"

    # 4) Rule-based parsing if LLM not used or failed
    if parsed is None:
        if doc_type == "W-2":
            parsed = parse_w2_from_text(raw_text)
        elif doc_type == "1099-INT":
            parsed = parse_1099_int_from_text(raw_text)
        elif doc_type == "1099-NEC":
            parsed = parse_1099_nec_from_text(raw_text)
        else:
            parsed = {
                "document_type": "UNKNOWN",
                "wages": 0.0,
                "interest_income": 0.0,
                "nonemployee_income": 0.0,
                "federal_withholding": 0.0,
            }

    # 5) Attach parser_used so app.py can display it
    parsed["parser_used"] = parser_used

    return {
        "document_type": parsed["document_type"],
        "parsed": parsed,
        "raw_text": raw_text,
    }
