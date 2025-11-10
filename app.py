# app.py

import base64
import streamlit as st

from parsers.document_router import analyze_document
from tax_calculator import TaxInput, compute_tax_summary
from form_1040_generator import generate_1040_pdf


st.set_page_config(
    page_title="AI Tax Return Agent (Prototype)",
    page_icon="💸",
    layout="centered",
)

st.markdown(
    """
    <style>
    iframe {max-width: 100%;}
    [data-testid="stTable"] {overflow-x: auto;}
    button, input, select, textarea {font-size: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("💸 AI Tax Return Agent (Prototype)")
st.write("Upload your W-2, 1099-INT, and 1099-NEC PDFs to generate a draft Form 1040.")
st.info("⚠️ Prototype only — not tax advice or a production filing tool.")

# Initialize session state for results
if "calc_results" not in st.session_state:
    st.session_state["calc_results"] = None


# 1. Personal Information
st.header("1. Personal Information")

col1, col2 = st.columns(2)
with col1:
    first_name = st.text_input("Your first name", "")
    middle_initial = st.text_input("Your middle initial", "")
    last_name = st.text_input("Your last name", "")

with col2:
    filing_status = st.selectbox(
        "Filing status",
        [
            "Single",
            "Married Filing Jointly",
            "Married Filing Separately",
            "Head of Household",
            "Qualifying surviving spouse (QSS)",
        ],
    )

# Spouse name – ONLY when relevant
spouse_first_name = ""
spouse_middle_initial = ""
spouse_last_name = ""

if filing_status in ("Married Filing Jointly", "Married Filing Separately"):
    st.subheader("Spouse information")
    col_s1, col_s2 = st.columns(2)
    with col_s1:
        spouse_first_name = st.text_input("Spouse first name", "")
        spouse_middle_initial = st.text_input("Spouse middle initial", "")
    with col_s2:
        spouse_last_name = st.text_input("Spouse last name", "")

# Text for the “If you checked MFS/HOH/QSS…” line
mfs_spouse_name_line = ""
hoh_qss_child_name = ""

if filing_status == "Married Filing Separately":
    default_spouse_line = " ".join(
        p for p in [spouse_first_name, spouse_middle_initial, spouse_last_name] if p
    ).strip()
    mfs_spouse_name_line = st.text_input(
        "If MFS, spouse name for filing-status line", default_spouse_line
    )

if filing_status in ("Head of Household", "Qualifying surviving spouse (QSS)"):
    hoh_qss_child_name = st.text_input(
        "If HOH or QSS, child's name for filing-status line", ""
    )

# Address block
st.subheader("Mailing Address")

col_addr1, col_addr2 = st.columns([3, 1])
with col_addr1:
    address = st.text_input("Home address (number and street)", "")
with col_addr2:
    apt_no = st.text_input("Apt. no.", "")

col_addr3, col_addr4, col_addr5 = st.columns([3, 1, 1])
with col_addr3:
    city = st.text_input("City or town", "")
with col_addr4:
    state = st.text_input("State", "")
with col_addr5:
    zipcode = st.text_input("ZIP code", "")

st.subheader("Foreign Address (if applicable)")
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    foreign_country = st.text_input("Foreign country name", "")
with col_f2:
    foreign_province = st.text_input("Foreign province/state/county", "")
with col_f3:
    foreign_postal_code = st.text_input("Foreign postal code", "")

# Last checkbox (nonresident / dual-status spouse)
st.subheader("Special elections")
treat_nra_spouse = st.checkbox(
    "Treating a nonresident or dual-status alien spouse as a U.S. resident for the entire year?",
    value=False,
)
nra_spouse_name = ""
if treat_nra_spouse:
    nra_spouse_name = st.text_input(
        "Name to print on the last filing-status line", ""
    )

dependents = st.number_input(
    "Number of dependents",
    min_value=0,
    max_value=20,
    value=0,
    step=1,
)

st.write("")

# 2. Input Mode
st.header("2. Choose Input Mode")

mode = st.radio(
    "How do you want to provide your income data?",
    ["Use uploaded PDFs (real forms)", "Enter income manually (debug mode)"],
)

uploaded_files = None
manual_wages = 0.0
manual_interest = 0.0
manual_nonemployee = 0.0
manual_withholding = 0.0

if mode == "Use uploaded PDFs (real forms)":
    st.subheader("Upload Original Tax Documents (PDF)")
    uploaded_files = st.file_uploader(
        "Upload your actual W-2, 1099-INT, and 1099-NEC PDFs (multiple files allowed)",
        type=["pdf"],
        accept_multiple_files=True,
    )
else:
    st.subheader("Manual Income Entry (Debug / Verification)")
    st.info("Use this mode to test tax calculations without relying on PDF parsing.")
    manual_wages = st.number_input(
        "Wages (W-2)",
        min_value=0.0,
        step=1000.0,
        value=50000.0,
    )
    manual_interest = st.number_input(
        "Interest income (1099-INT)",
        min_value=0.0,
        step=100.0,
        value=0.0,
    )
    manual_nonemployee = st.number_input(
        "Nonemployee income (1099-NEC)",
        min_value=0.0,
        step=1000.0,
        value=0.0,
    )
    manual_withholding = st.number_input(
        "Federal withholding",
        min_value=0.0,
        step=500.0,
        value=5000.0,
    )

st.write("")
process_button = st.button("Process & Calculate Tax")


# MAIN PROCESSING LOGIC

if process_button:
    file_results = []
    ssn_from_w2 = ""
    success = False

    if mode == "Use uploaded PDFs (real forms)":
        if not uploaded_files:
            st.error("Please upload at least one PDF file before processing.")
        else:
            with st.spinner("Parsing documents and calculating tax..."):
                total_wages = 0.0
                total_interest = 0.0
                total_nonemployee = 0.0
                total_withholding = 0.0

                for f in uploaded_files:
                    # Extra safety: enforce PDF MIME type and size limit (e.g., 5 MB)
                    if f.type not in ("application/pdf", "application/x-pdf"):
                        st.warning(
                            f"Skipping {f.name}: not recognized as a PDF (type={f.type})."
                        )
                        continue

                    if getattr(f, "size", None) and f.size > 5 * 1024 * 1024:
                        st.warning(
                            f"Skipping {f.name}: file is larger than 5 MB."
                        )
                        continue

                    file_bytes = f.read()

                    try:
                        result = analyze_document(file_bytes)
                    except Exception as e:
                        st.error(f"Error parsing {f.name}: {e}")
                        continue

                    file_results.append(
                        {
                            "filename": f.name,
                            "document_type": result["document_type"],
                            "parsed": result["parsed"],
                            "raw_text": result["raw_text"],
                        }
                    )

                    p = result["parsed"]
                    total_wages += float(p.get("wages", 0.0))
                    total_interest += float(p.get("interest_income", 0.0))
                    total_nonemployee += float(p.get("nonemployee_income", 0.0))
                    total_withholding += float(p.get("federal_withholding", 0.0))

                    # Only take SSN from W-2, if parser provides it
                    if result["document_type"] == "W-2" and not ssn_from_w2:
                        ssn_from_w2 = p.get("ssn", "") or ""

                tax_input = TaxInput(
                    wages=total_wages,
                    interest_income=total_interest,
                    nonemployee_income=total_nonemployee,
                    federal_withholding=total_withholding,
                    filing_status=filing_status,
                    dependents=dependents,
                )
                tax_summary = compute_tax_summary(tax_input)
                success = True
    else: 
        with st.spinner("Calculating tax from manual inputs..."):
            total_wages = manual_wages
            total_interest = manual_interest
            total_nonemployee = manual_nonemployee
            total_withholding = manual_withholding

            tax_input = TaxInput(
                wages=total_wages,
                interest_income=total_interest,
                nonemployee_income=total_nonemployee,
                federal_withholding=total_withholding,
                filing_status=filing_status,
                dependents=dependents,
            )
            tax_summary = compute_tax_summary(tax_input)
        file_results = []
        ssn_from_w2 = ""
        success = True

    if success:
        st.session_state["calc_results"] = {
            "mode": mode,
            "total_wages": total_wages,
            "total_interest": total_interest,
            "total_nonemployee": total_nonemployee,
            "total_withholding": total_withholding,
            "tax_summary": tax_summary,
            "file_results": file_results,
            "ssn_from_w2": ssn_from_w2,
        }

        if mode == "Use uploaded PDFs (real forms)":
            st.success(
                "✅ Documents processed successfully. Scroll down to see your tax summary and Form 1040 preview."
            )
        else:
            st.success(
                "✅ Manual inputs processed successfully. Scroll down to see your tax summary and Form 1040 preview."
            )


# DISPLAY RESULTS + PREVIEW / DOWNLOAD

results = st.session_state.get("calc_results")

if results is not None:
    mode = results["mode"]
    total_wages = results["total_wages"]
    total_interest = results["total_interest"]
    total_nonemployee = results["total_nonemployee"]
    total_withholding = results["total_withholding"]
    tax_summary = results["tax_summary"]
    file_results = results["file_results"]
    ssn_from_w2 = results["ssn_from_w2"]

    tab_summary, tab_details = st.tabs(["📊 Summary & Form 1040", "🔍 Parsing details"])

    # TAB 1: SUMMARY + PREVIEW
    with tab_summary:
        st.subheader("3. Income Summary Used for Tax Calculation")
        st.table(
            {
                "Category": [
                    "Wages (W-2)",
                    "Interest income (1099-INT)",
                    "Nonemployee income (1099-NEC)",
                    "Federal withholding",
                ],
                "Amount": [
                    f"${total_wages:,.2f}",
                    f"${total_interest:,.2f}",
                    f"${total_nonemployee:,.2f}",
                    f"${total_withholding:,.2f}",
                ],
            }
        )

        # --- Tax Summary Metrics ---
        st.subheader("4. Tax Summary")

        col_a, col_b = st.columns(2)
        with col_a:
            st.metric("Total Income", f"${tax_summary['total_income']:,.2f}")
            st.metric("Taxable Income", f"${tax_summary['taxable_income']:,.2f}")
            st.metric(
                "Tax Liability",
                f"${tax_summary.get('tax_liability', tax_summary.get('tax_liility', 0.0)):,.2f}",
            )
        with col_b:
            st.metric(
                "Federal Withholding",
                f"${tax_summary['federal_withholding']:,.2f}",
            )
            st.metric("Refund", f"${tax_summary['refund']:,.2f}")
            st.metric("Amount Owed", f"${tax_summary['amount_owed']:,.2f}")

        # --- Build personal_info & income_data for PDF ---
        personal_info = {
            "first_name": first_name,
            "middle_initial": middle_initial,
            "last_name": last_name,
            "spouse_first_name": spouse_first_name,
            "spouse_middle_initial": spouse_middle_initial,
            "spouse_last_name": spouse_last_name,
            "mfs_spouse_name_line": mfs_spouse_name_line,
            "hoh_qss_child_name": hoh_qss_child_name,
            "filing_status": filing_status,
            "dependents": dependents,
            "address": address,
            "apt_no": apt_no,
            "city": city,
            "state": state,
            "zipcode": zipcode,
            "foreign_country": foreign_country,
            "foreign_province": foreign_province,
            "foreign_postal_code": foreign_postal_code,
            "treat_nra_spouse": treat_nra_spouse,
            "nra_spouse_name": nra_spouse_name,
            "ssn": ssn_from_w2,
        }

        income_data = {
            "wages": total_wages,
            "interest_income": total_interest,
            "nonemployee_income": total_nonemployee,
            "federal_withholding": total_withholding,
        }

        pdf_buffer = generate_1040_pdf(personal_info, income_data, tax_summary)
        pdf_bytes = pdf_buffer.getvalue()
        b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        st.subheader("5. Preview & Download Draft Form 1040")

        st.markdown(
            f"""
            <div style="border:1px solid #ddd;border-radius:4px;overflow:hidden;">
                <iframe src="data:application/pdf;base64,{b64_pdf}"
                        width="100%" height="700px" style="border:none;"
                        type="application/pdf"></iframe>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.download_button(
            label="📄 Download Draft Form 1040",
            data=pdf_bytes,
            file_name="form_1040_prototype.pdf",
            mime="application/pdf",
        )

    # TAB 2: PARSING DETAILS
    with tab_details:
        if mode == "Use uploaded PDFs (real forms)" and file_results:
            st.subheader("Per-file Extraction Details (Validation on Original Forms)")
            for file_result in file_results:
                st.markdown(
                    f"**File:** `{file_result['filename']}`  |  Detected type: `{file_result['document_type']}`"
                )
                st.write("Parsed fields used from this file:")

                # Mask SSN if present before showing parsed data
                parsed_safe = dict(file_result["parsed"])
                if "ssn" in parsed_safe and parsed_safe["ssn"]:
                    s = str(parsed_safe["ssn"])
                    parsed_safe["ssn"] = "***-**-" + s[-4:]

                # Warn in UI if everything came out as zero
                w = float(parsed_safe.get("wages", 0.0))
                i = float(parsed_safe.get("interest_income", 0.0))
                n = float(parsed_safe.get("nonemployee_income", 0.0))
                fw = float(parsed_safe.get("federal_withholding", 0.0))

                if w == 0 and i == 0 and n == 0 and fw == 0:
                    st.warning(
                        "No numeric income values were detected in this file. "
                        "It may be blank, scanned/low-quality, or not in the expected format."
                    )

                st.json(parsed_safe)


                with st.expander("Show raw extracted text from this PDF"):
                    st.text(file_result["raw_text"])
        elif mode == "Enter income manually (debug mode)":
            st.info("Manual mode: no PDF parsing details to show.")
