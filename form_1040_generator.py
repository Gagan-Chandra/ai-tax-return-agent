# form_1040_generator.py

from io import BytesIO
from typing import Dict, Any

from reportlab.pdfgen import canvas
from PyPDF2 import PdfReader, PdfWriter

TEMPLATE_PATH = "f1040.pdf"


def _fmt_money(value: float) -> str:
    """Format a number with commas and 2 decimals."""
    return f"{value:,.2f}"


def generate_1040_pdf(
    personal_info: Dict[str, Any],
    income_data: Dict[str, float],
    tax_summary: Dict[str, float],
) -> BytesIO:
    """
    Overlay key tax data + identity info onto the original Form 1040 PDF (f1040.pdf)
    and return the filled PDF as an in-memory buffer.
    """

    first_name = (personal_info.get("first_name") or "").strip()
    middle_initial = (personal_info.get("middle_initial") or "").strip()
    last_name = (personal_info.get("last_name") or "").strip()

    spouse_first_name = (personal_info.get("spouse_first_name") or "").strip()
    spouse_middle_initial = (personal_info.get("spouse_middle_initial") or "").strip()
    spouse_last_name = (personal_info.get("spouse_last_name") or "").strip()

    mfs_spouse_name_line = (personal_info.get("mfs_spouse_name_line") or "").strip()
    hoh_qss_child_name = (personal_info.get("hoh_qss_child_name") or "").strip()
    treat_nra_spouse = bool(personal_info.get("treat_nra_spouse"))
    nra_spouse_name = (personal_info.get("nra_spouse_name") or "").strip()

    address = (personal_info.get("address") or "").strip()
    apt_no = (personal_info.get("apt_no") or "").strip()
    city = (personal_info.get("city") or "").strip()
    state = (personal_info.get("state") or "").strip()
    zipcode = (personal_info.get("zipcode") or "").strip()

    foreign_country = (personal_info.get("foreign_country") or "").strip()
    foreign_province = (personal_info.get("foreign_province") or "").strip()
    foreign_postal_code = (personal_info.get("foreign_postal_code") or "").strip()

    ssn_input = (personal_info.get("ssn") or "").strip()
    filing_status = personal_info.get("filing_status", "")

    wages = float(income_data.get("wages", 0.0))
    interest_income = float(income_data.get("interest_income", 0.0))
    nonemployee_income = float(income_data.get("nonemployee_income", 0.0))
    total_withholding = float(income_data.get("federal_withholding", 0.0))

    total_income = float(tax_summary.get("total_income", 0.0))
    standard_deduction = float(tax_summary.get("standard_deduction", 0.0))
    taxable_income = float(tax_summary.get("taxable_income", 0.0))
    tax_liability = float(tax_summary.get("tax_liability", 0.0))
    refund = float(tax_summary.get("refund", 0.0))
    amount_owed = float(tax_summary.get("amount_owed", 0.0))

    adjusted_gross_income = total_income
    total_payments = total_withholding

    template_reader = PdfReader(TEMPLATE_PATH)
    first_page = template_reader.pages[0]
    mediabox = first_page.mediabox
    page_width = float(mediabox.width)
    page_height = float(mediabox.height)

    overlay_packet = BytesIO()
    c = canvas.Canvas(overlay_packet, pagesize=(page_width, page_height))

    # X-position for numeric amount column
    amount_x = page_width - 55 

    # PAGE 1 overlay
    c.setFont("Helvetica", 10)

    #Name & SSN
    taxpayer_name_left = " ".join(
        part for part in [first_name, middle_initial] if part
    ).strip()
    if not taxpayer_name_left and last_name:
        taxpayer_name_left = last_name 

    if not taxpayer_name_left and not last_name:
        taxpayer_name_left = ""
        last_name = ""

    name_y = page_height - 95
    c.setFont("Helvetica-Bold", 10)
    c.drawString(55, name_y, taxpayer_name_left)
    c.drawString(255, name_y, last_name)

    spouse_name_left = " ".join(
        part for part in [spouse_first_name, spouse_middle_initial] if part
    ).strip()
    if spouse_name_left or spouse_last_name:
        spouse_y = page_height - 120
        c.drawString(55, spouse_y, spouse_name_left)
        c.drawString(255, spouse_y, spouse_last_name)

    ssn_clean = ssn_input.replace("-", "")
    if not ssn_clean:
        ssn_clean = "000000000"

    ssn_digits = ssn_clean[:9].ljust(9, "0")
    g1 = ssn_digits[0:3]
    g2 = ssn_digits[3:5]
    g3 = ssn_digits[5:9]

    c.setFont("Helvetica-Bold", 11)

    base_x = page_width - 134   
    base_y = page_height - 99 
    digit_spacing = 6
    group_gap = 8

    x = base_x
    for ch in g1:
        c.drawString(x, base_y, ch)
        x += digit_spacing

    x += group_gap
    for ch in g2:
        c.drawString(x, base_y, ch)
        x += digit_spacing

    x += group_gap
    for ch in g3:
        c.drawString(x, base_y, ch)
        x += digit_spacing

    # 3b. Address lines
    c.setFont("Helvetica-Bold", 10)

    addr_y = page_height - 146
    city_y = page_height - 170
    foreign_y = page_height - 195

    if address:
        c.drawString(55, addr_y, address)
    if apt_no:
        c.drawString(420, addr_y, apt_no)

    if city:
        c.drawString(55, city_y, city)
    if state:
        c.drawString(345, city_y, state)
    if zipcode:
        c.drawString(410, city_y, zipcode)

    if foreign_country or foreign_province or foreign_postal_code:
        if foreign_country:
            c.drawString(55, foreign_y, foreign_country)
        if foreign_province:
            c.drawString(310, foreign_y, foreign_province)
        if foreign_postal_code:
            c.drawString(410, foreign_y, foreign_postal_code)

    # 3c. Filing status===
    # Left column check boxes: Single, MFJ, MFS
    status_left_x = 103
    # Right column check boxes: HOH, QSS
    status_right_x = 370

    # Y positions (measured off your form screenshot)
    status_single_y = page_height - 210    # Single
    status_mfj_y    = page_height - 221    # Married filing jointly
    status_mfs_y    = page_height - 233    # Married filing separately
    status_hoh_y    = page_height - 210    # Head of household
    status_qss_y    = page_height - 233    # Qualifying surviving spouse

    c.setFont("Helvetica-Bold", 10)

    if filing_status == "Single":
        c.drawString(status_left_x, status_single_y, "X")
    elif filing_status == "Married Filing Jointly":
        c.drawString(status_left_x, status_mfj_y, "X")
    elif filing_status == "Married Filing Separately":
        c.drawString(status_left_x, status_mfs_y, "X")
    elif filing_status == "Head of Household":
        c.drawString(status_right_x, status_hoh_y, "X")
    elif filing_status == "Qualifying surviving spouse (QSS)":
        c.drawString(status_right_x, status_qss_y, "X")

    # 3c.1 Text on the "If you checked MFS / HOH / QSS" line
    # This is the dashed line right under the status boxes.
    line_mfs_hoh_y = page_height - 254 

    c.setFont("Helvetica-Bold", 9.5)
    text_on_line = ""
    if filing_status == "Married Filing Separately":
        # Prefer explicit override, otherwise default to spouse full name
        if mfs_spouse_name_line:
            text_on_line = mfs_spouse_name_line
        else:
            text_on_line = " ".join(
                p
                for p in [spouse_first_name, spouse_middle_initial, spouse_last_name]
                if p
            ).strip()
    elif filing_status in ("Head of Household", "Qualifying surviving spouse (QSS)"):
        text_on_line = hoh_qss_child_name

    if text_on_line:
        c.drawString(310, line_mfs_hoh_y, text_on_line)

    # 3c.2 Last checkbox: nonresident/dual-status spouse treated as U.S. resident 
    nra_box_x = status_left_x      
    nra_box_y = page_height - 267 
    nra_name_y = page_height - 281

    if treat_nra_spouse:
        c.setFont("Helvetica-Bold", 10)
        c.drawString(nra_box_x, nra_box_y, "X")
        if nra_spouse_name:
            c.setFont("Helvetica-Bold", 9.5)
            c.drawString(340, nra_name_y, nra_spouse_name)

    # 3d. Income lines – fine-tuned to 2024 Form 1040 
    line1_y = 355    # Line 1: Wages
    line2b_y = 236   # Line 2b: Taxable interest
    line8_y = 153    # Line 8: Other income
    line9_y = 140    # Line 9: Total income
    line11_y = 115   # Line 11: AGI
    line12_y = 104   # Line 12: Standard deduction
    line15_y = 69    # Line 15: Taxable income

    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(amount_x, line1_y, _fmt_money(wages))
    c.drawRightString(amount_x, line2b_y, _fmt_money(interest_income))
    c.drawRightString(amount_x, line8_y, _fmt_money(nonemployee_income))
    c.drawRightString(amount_x, line9_y, _fmt_money(total_income))
    c.drawRightString(amount_x, line11_y, _fmt_money(adjusted_gross_income))
    c.drawRightString(amount_x, line12_y, _fmt_money(standard_deduction))
    c.drawRightString(amount_x, line15_y, _fmt_money(taxable_income))

    c.showPage()

    # 4. PAGE 2 overlay
    c.setFont("Helvetica-Bold", 10)

    line24_y = 650   # Line 24: total tax
    line33_y = 494.5   # Line 33: total payments
    line34_y = 481   # Line 34: refund
    line37_y = 410   # Line 37: amount you owe

    c.drawRightString(amount_x, line24_y, _fmt_money(tax_liability))
    c.drawRightString(amount_x, line33_y, _fmt_money(total_payments))
    c.drawRightString(amount_x, line34_y, _fmt_money(refund))
    c.drawRightString(amount_x, line37_y, _fmt_money(amount_owed))

    c.showPage()
    c.save()
    overlay_packet.seek(0)

    overlay_reader = PdfReader(overlay_packet)

    # 5. Merge overlay onto template
    writer = PdfWriter()
    for i, base_page in enumerate(template_reader.pages):
        page = base_page
        if i < len(overlay_reader.pages):
            overlay_page = overlay_reader.pages[i]
            page.merge_page(overlay_page)
        writer.add_page(page)

    # 6. Return final PDF 
    output_buffer = BytesIO()
    writer.write(output_buffer)
    output_buffer.seek(0)
    return output_buffer
