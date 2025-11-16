# app.py

import base64
import streamlit as st

from parsers.document_router import analyze_document
from tax_calculator import TaxInput, compute_tax_summary, compute_self_employment_tax
from form_1040_generator import generate_1040_pdf
from ai_advisor import ask_tax_advisor  # NEW


st.set_page_config(
    page_title="AI Tax Return Agent (Prototype)",
    page_icon="💸",
    layout="centered",
)

# Mobile-friendly tweaks
st.markdown(
    """
    <style>
    iframe {max-width: 100%;}         /* Make PDF preview responsive */
    [data-testid="stTable"] {overflow-x: auto;}
    button, input, select, textarea {font-size: 1rem;}
    </style>
    """,
    unsafe_allow_html=True,
)

st.title("💸 AI Tax Return Agent (Prototype)")
st.write(
    "Upload your W-2, 1099-INT, and 1099-NEC PDFs to generate a draft Form 1040."
)
st.info("⚠️ Prototype only — not tax advice or a production filing tool.")

# Initialize session state for results
if "calc_results" not in st.session_state:
    st.session_state["calc_results"] = None


# ============================
# 1. Personal Information
# ============================
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


# ============================
# 2. Input Mode
# ============================
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

# default in case of manual mode
use_llm_parsing = False

if mode == "Use uploaded PDFs (real forms)":
    st.subheader("Upload Original Tax Documents (PDF)")

    col_upload, col_engine = st.columns([2, 2])
    with col_upload:
        uploaded_files = st.file_uploader(
            "Upload your actual W-2, 1099-INT, and 1099-NEC PDFs (multiple files allowed)",
            type=["pdf"],
            accept_multiple_files=True,
        )
    with col_engine:
        parsing_engine = st.radio(
            "Parsing engine",
            [
                "Rule-based (pdfplumber + Tesseract, local only)",
                "Groq Vision LLM (experimental)",
            ],
            help=(
                "Rule-based: stays on this machine using pdfplumber + Tesseract. "
                "Groq Vision LLM: sends a text-only representation of each page to Groq for parsing."
            ),
        )
        use_llm_parsing = parsing_engine.startswith("Groq")

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


# ============================
# MAIN PROCESSING LOGIC
# ============================
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
                    # Basic safety: enforce PDF MIME type and size limit (e.g., 5 MB)
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
                        result = analyze_document(
                            file_bytes,
                            use_llm=use_llm_parsing,
                        )
                    except TypeError:
                        # Backward-compat if analyze_document doesn't support use_llm yet
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
                            "parser_used": result.get(
                                "parser_used",
                                "rule-based",
                            ),
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


# ============================
# DISPLAY RESULTS + PREVIEW
# ============================
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

    # Build income_data once so we can reuse it for AI Advisor and 1040 PDF
    income_data = {
        "wages": total_wages,
        "interest_income": total_interest,
        "nonemployee_income": total_nonemployee,
        "federal_withholding": total_withholding,
    }

    tab_summary, tab_ai_advisor, tab_details = st.tabs(
        ["📊 Summary & Form 1040", "🧠 AI Tax Advisor (Groq)", "🔍 Parsing details"]
    )

    # =======================
    # TAB 1: SUMMARY + 1040
    # =======================
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

        # --- 4. Tax Summary Metrics ---
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

        # ---------------------------
        # 5. Tax Insights (bullets)
        # ---------------------------
        st.subheader("5. Tax Insights & Explanation")

        total_income = tax_summary["total_income"]
        std_ded = tax_summary["standard_deduction"]
        taxable_income = tax_summary["taxable_income"]
        tax_liability = tax_summary.get(
            "tax_liability", tax_summary.get("tax_liility", 0.0)
        )
        effective_rate = (
            tax_liability / total_income * 100.0 if total_income > 0 else 0.0
        )

        # Format money with escaped '$' so Markdown doesn't think it's LaTeX.
        wages_str = f"\\${total_wages:,.0f}"
        interest_str = f"\\${total_interest:,.0f}"
        nonemp_str = f"\\${total_nonemployee:,.0f}"
        total_income_str = f"\\${total_income:,.0f}"
        std_ded_str = f"\\${std_ded:,.0f}"
        taxable_income_str = f"\\${taxable_income:,.0f}"
        tax_liability_str = f"\\${tax_liability:,.0f}"
        withholding_str = f"\\${total_withholding:,.0f}"
        refund_str = f"\\${tax_summary['refund']:,.0f}"
        owed_str = f"\\${tax_summary['amount_owed']:,.0f}"

        explanation_lines = [
            f"- **Income sources:** wages {wages_str}, interest {interest_str}, "
            f"and nonemployee income {nonemp_str}.",
            f"- **Total income:** {total_income_str}.",
            f"- **Standard deduction** for **{filing_status}**: {std_ded_str}, "
            f"leaving **taxable income** of {taxable_income_str}.",
            "- We then apply the **2024 progressive federal tax brackets** "
            "(10%, 12%, 22%, 24%, 32%, 35%, 37%) to that taxable income.",
            f"- This results in a **tax liability** of {tax_liability_str}, "
            f"which is an **effective tax rate** of about {effective_rate:.1f}% "
            "of your total income.",
        ]

        if tax_summary["refund"] > 0:
            explanation_lines.append(
                f"- Because your federal withholding ({withholding_str}) was "
                f"**higher** than your tax liability, you get a **refund** of {refund_str}."
            )
        elif tax_summary["amount_owed"] > 0:
            explanation_lines.append(
                f"- Because your federal withholding ({withholding_str}) was "
                f"**less** than your tax liability, you **owe** an additional {owed_str}."
            )
        else:
            explanation_lines.append(
                "- Your withholding exactly matched your tax liability, so there is "
                "no refund and no additional amount owed."
            )

        st.markdown("\n".join(explanation_lines))

        # ---------------------------
        # 6. Detailed Calculation
        # ---------------------------
        st.subheader("6. Detailed Calculation (Numbers)")

        calc_lines = [
            "1) Total income",
            "   = wages + interest + nonemployee",
            f"   = {total_wages:,.2f} + {total_interest:,.2f} + {total_nonemployee:,.2f}",
            f"   = {total_income:,.2f}",
            "",
            "2) Standard deduction",
            "   = standard deduction by filing status",
            f"   = {std_ded:,.2f}",
            "",
            "3) Taxable income",
            "   = max(0, total income - standard deduction)",
            f"   = max(0, {total_income:,.2f} - {std_ded:,.2f})",
            f"   = {taxable_income:,.2f}",
            "",
            "4) Tax liability",
            "   = tax after applying 2024 progressive brackets",
            f"   = {tax_liability:,.2f}",
            "",
            "5) Compare to withholding",
            "   Refund     = max(0, withholding - liability)",
            f"             = max(0, {tax_summary['federal_withholding']:,.2f} - {tax_liability:,.2f})",
            f"             = {tax_summary['refund']:,.2f}",
            "",
            "   Amount owed = max(0, liability - withholding)",
            f"              = max(0, {tax_liability:,.2f} - {tax_summary['federal_withholding']:,.2f})",
            f"              = {tax_summary['amount_owed']:,.2f}",
        ]

        st.code("\n".join(calc_lines), language="text")

                # ---------------------------
        # 7. How Your Tax Liability Was Calculated (Bracket Breakdown)
        # ---------------------------
        st.subheader("7. How Your Tax Liability Was Calculated (Bracket Breakdown)")

        taxable = taxable_income
        se_total_tax = float(tax_summary.get("se_total_tax", 0.0))

        # 2024 brackets (currently using Single brackets for demo;
        # can be extended per filing status if you want).
        brackets = [
            (0, 11600, 0.10),
            (11600, 47150, 0.12),
            (47150, 100525, 0.22),
            (100525, 191950, 0.24),
            (191950, 243725, 0.32),
            (243725, 609350, 0.35),
            (609350, float("inf"), 0.37),
        ]

        bracket_results = []
        remaining = taxable

        for lower, upper, rate in brackets:
            if taxable <= lower:
                break

            amount_in_bracket = min(upper - lower, remaining)
            if amount_in_bracket <= 0:
                continue

            tax_for_bracket = amount_in_bracket * rate
            bracket_results.append(
                (f"{int(rate * 100)}%", amount_in_bracket, tax_for_bracket)
            )
            remaining -= amount_in_bracket
            if remaining <= 0:
                break

        st.markdown("### 📘 Federal Tax Bracket Contribution (2024)")

        if bracket_results:
            st.table(
                {
                    "Bracket Rate": [row[0] for row in bracket_results],
                    "Taxable Amount": [f"${row[1]:,.2f}" for row in bracket_results],
                    "Tax Owed": [f"${row[2]:,.2f}" for row in bracket_results],
                }
            )

            # This is the income-tax portion only (no self-employment tax)
            total_bracket_tax = sum(row[2] for row in bracket_results)

            if se_total_tax > 0:
                combined_tax = total_bracket_tax + se_total_tax

                st.markdown(
                    f"""
                    **Income tax from brackets (no self-employment tax):**  
                    <span style="font-size: 1.1rem"><b>${total_bracket_tax:,.2f}</b></span>  

                    **+ Self-employment tax (Schedule SE prototype):**  
                    <span style="font-size: 1.1rem"><b>${se_total_tax:,.2f}</b></span>  

                    **= Total tax liability (section 4):**  
                    <span style="font-size: 1.3rem"><b>${combined_tax:,.2f}</b></span>  

                    <i>(This sum matches the "Tax Liability" metric above, up to rounding.)</i>
                    """,
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"""
                    **Total tax computed from brackets:**  
                    <span style="font-size: 1.3rem"><b>${total_bracket_tax:,.2f}</b></span>  

                    <i>(Matches the "Tax Liability" metric in section 4, up to rounding, 
                    since there is no self-employment tax.)</i>
                    """,
                    unsafe_allow_html=True,
                )
        else:
            st.info("No taxable income, so no tax is owed under the bracket system.")


        # ==========================
        # 8. Self-Employment Tax
        # ==========================
        if total_nonemployee > 0:
            st.subheader("8. Self-Employment Tax (Schedule SE prototype)")

            se = compute_self_employment_tax(total_nonemployee)

            se_net = se["se_net_earnings"]
            se_ss = se["se_ss_tax"]
            se_med = se["se_medicare_tax"]
            se_total = se["se_total_tax"]
            se_ded = se["se_deduction"]

            st.markdown(
                f"""
                This section shows how **self-employment tax** is computed when you
                have nonemployee income (for example, from 1099-NEC).

                - **Nonemployee income (gross)**: ${total_nonemployee:,.2f}  
                - **Net earnings from self-employment (92.35%)**: ${se_net:,.2f}  
                - **Social Security part (12.4% up to wage base)**: ${se_ss:,.2f}  
                - **Medicare part (2.9%)**: ${se_med:,.2f}  
                - **Total self-employment tax (Schedule SE line 12)**: ${se_total:,.2f}  
                - **Deductible part (Schedule 1, ½ of SE tax)**: ${se_ded:,.2f}  
                """,
                unsafe_allow_html=True,
            )

        else:
            st.subheader("8. Self-Employment Tax (Schedule SE prototype)")
            st.info("No nonemployee income detected — self-employment tax does not apply.")

        # ==============================
        # 9. Schedule 1 & Schedule B
        # ==============================
        st.subheader("9. Schedule 1 & Schedule B (Prototype)")

        schedule_lines = []

        # --- Schedule 1: Adjustments to income (prototype) ---
        if total_nonemployee > 0:
            se = compute_self_employment_tax(total_nonemployee)
            se_ded = float(se.get("se_deduction", 0.0))

            if se_ded > 0:
                se_ded_str = f"\\${se_ded:,.2f}"
                schedule_lines.append(
                    f"- **Schedule 1 – Adjustments to income:** "
                    f"we include the **deductible half of self-employment tax** "
                    f"({se_ded_str}) as an above-the-line adjustment in this prototype."
                )
            else:
                schedule_lines.append(
                    "- **Schedule 1 – Adjustments to income:** "
                    "nonemployee income is present, but the self-employment deduction "
                    "computed to zero in this toy example."
                )
        else:
            schedule_lines.append(
                "- **Schedule 1 – Adjustments to income:** "
                "in this prototype we only model the *half of self-employment tax* "
                "deduction. Other real-world adjustments like student loan interest, "
                "HSA contributions, and educator expenses are **not yet implemented**."
            )

        # --- Schedule B: Interest & ordinary dividends (prototype) ---
        if total_interest > 1500:
            interest_str = f"\\${total_interest:,.2f}"
            schedule_lines.append(
                f"- **Schedule B – Interest income:** your taxable interest is "
                f"{interest_str}, which is **more than \\$1,500**. In real IRS rules, "
                "that generally means you must attach **Schedule B** and list payers "
                "and amounts. In this prototype, we only calculate the total interest "
                "and flag that Schedule B would be required."
            )
        elif total_interest > 0:
            interest_str = f"\\${total_interest:,.2f}"
            schedule_lines.append(
                f"- **Schedule B – Interest income:** your total interest "
                f"({interest_str}) is **below the \\$1,500 threshold**, so Schedule B "
                "would typically **not** be required. We still carry this amount into "
                "Form 1040, line 2b."
            )
        else:
            schedule_lines.append(
                "- **Schedule B – Interest income:** no taxable interest detected, so "
                "Schedule B does not apply in this scenario."
            )

        st.markdown("\n".join(schedule_lines))

        # ---------------------------
        # 10. Data Quality Checks
        # ---------------------------
        st.subheader("10. Data Quality Checks & Potential Issues")

        checks = []

        # 1) SSN detection (only when using real PDFs)
        if mode == "Use uploaded PDFs (real forms)":
            if ssn_from_w2:
                checks.append(
                    (
                        "✅ OK",
                        "SSN detected on W-2",
                        "We were able to read an SSN from at least one W-2. "
                        "In the 1040 preview it is masked for safety.",
                    )
                )
            else:
                checks.append(
                    (
                        "⚠️ Review",
                        "Missing SSN on W-2",
                        "We could not confidently detect an SSN in the uploaded W-2. "
                        "You should manually verify the SSN on the final Form 1040.",
                    )
                )

        # 2) Schedule B threshold
        if total_interest > 1500:
            checks.append(
                (
                    "⚠️ Review",
                    "Schedule B likely required",
                    f"Taxable interest is ${total_interest:,.2f}, which is more than $1,500. "
                    "In a real filing you would attach Schedule B with payer details.",
                )
            )
        else:
            checks.append(
                (
                    "✅ OK",
                    "Schedule B threshold",
                    f"Taxable interest is ${total_interest:,.2f}, at or below the $1,500 "
                    "threshold where Schedule B is typically required.",
                )
            )

        # 3) Withholding vs liability sanity
        if total_income > 0 and tax_liability == 0:
            checks.append(
                (
                    "⚠️ Review",
                    "Zero tax liability with positive income",
                    "Total income is positive but computed tax liability is zero. "
                    "This can be valid in special cases but is worth double-checking.",
                )
            )
        else:
            if tax_liability > 0:
                ratio = total_withholding / tax_liability if tax_liability > 0 else 0
                if ratio > 2.0:
                    checks.append(
                        (
                            "⚠️ Review",
                            "Very high withholding vs liability",
                            "Federal withholding is more than 2× your computed tax liability. "
                            "Large refunds can indicate incorrect W-4 settings or input issues.",
                        )
                    )
                else:
                    checks.append(
                        (
                            "✅ OK",
                            "Withholding vs liability",
                            "Withholding and tax liability are in a broadly reasonable range.",
                        )
                    )

        # 4) Negative amounts
        if (
            total_income < 0
            or total_wages < 0
            or total_interest < 0
            or total_nonemployee < 0
            or total_withholding < 0
        ):
            checks.append(
                (
                    "⚠️ Review",
                    "Negative amounts detected",
                    "One or more income or withholding values are negative. "
                    "This prototype does not fully handle loss / carryover scenarios.",
                )
            )

        # 5) Manual entry notice
        if mode == "Enter income manually (debug mode)":
            checks.append(
                (
                    "ℹ️ Info",
                    "Manual entry run",
                    "Values were entered manually, so we cannot cross-check them "
                    "against the original PDFs in this run.",
                )
            )

        # 6) Per-file empty parses
        for file_result in file_results:
            p = file_result["parsed"]
            w = float(p.get("wages", 0.0))
            i_ = float(p.get("interest_income", 0.0))
            n_ = float(p.get("nonemployee_income", 0.0))
            fw_ = float(p.get("federal_withholding", 0.0))
            if w == 0 and i_ == 0 and n_ == 0 and fw_ == 0:
                checks.append(
                    (
                        "⚠️ Review",
                        f"{file_result['filename']} produced no numeric values",
                        "The form may be blank, heavily scanned, or not in the expected layout.",
                    )
                )

        # 7) Missing document detector (Feature 3)
        if mode == "Use uploaded PDFs (real forms)":
            expected_forms = []
            if total_wages > 0:
                expected_forms.append("W-2")
            if total_interest > 0:
                expected_forms.append("1099-INT")
            if total_nonemployee > 0:
                expected_forms.append("1099-NEC")

            present_forms = {f["document_type"] for f in file_results}
            missing_forms = [f for f in expected_forms if f not in present_forms]

            if missing_forms:
                checks.append(
                    (
                        "⚠️ Review",
                        "Potential missing source forms",
                        "We detect income that usually comes from these forms, but they "
                        f"weren't among the uploads: {', '.join(missing_forms)}. "
                        "This can simply mean some income was entered manually, but in a "
                        "real filing you'd want to confirm all forms are present.",
                    )
                )
            elif expected_forms:
                checks.append(
                    (
                        "✅ OK",
                        "Uploaded forms match detected income types",
                        "For each income type we detected (wages, interest, nonemployee), "
                        "we see a corresponding W-2/1099 form among your uploads.",
                    )
                )

        quality_table = {
            "Status": [c[0] for c in checks],
            "Item": [c[1] for c in checks],
            "Details": [c[2] for c in checks],
        }
        st.table(quality_table)

        # ---------------------------
        # 11. Scenario Comparison
        # ---------------------------
        st.subheader("11. Scenario Comparison: Base vs Alternative")

        st.write(
            "Define an alternative scenario (Scenario B) to compare against the base case. "
            "This is useful for planning: raises, bonuses, or changing withholding."
        )

        col_sc1, col_sc2 = st.columns(2)
        with col_sc1:
            alt_income_change = st.number_input(
                "Change in total income (Scenario B vs Base)",
                value=0.0,
                step=1000.0,
                format="%.2f",
                help="Positive = more income, negative = less income",
            )
        with col_sc2:
            alt_withholding_change = st.number_input(
                "Change in federal withholding (Scenario B vs Base)",
                value=0.0,
                step=500.0,
                format="%.2f",
                help="Positive = more withheld, negative = less withheld",
            )

        alt_filing_status_option = st.selectbox(
            "Scenario B filing status",
            [
                "Same as base",
                "Single",
                "Married Filing Jointly",
                "Married Filing Separately",
                "Head of Household",
                "Qualifying surviving spouse (QSS)",
            ],
            index=0,
        )

        scenario_b_filing_status = (
            filing_status
            if alt_filing_status_option == "Same as base"
            else alt_filing_status_option
        )

        if (
            alt_income_change != 0.0
            or alt_withholding_change != 0.0
            or alt_filing_status_option != "Same as base"
        ):
            alt_input = TaxInput(
                wages=total_wages + alt_income_change,
                interest_income=total_interest,
                nonemployee_income=total_nonemployee,
                federal_withholding=total_withholding + alt_withholding_change,
                filing_status=scenario_b_filing_status,
                dependents=dependents,
            )
            alt_summary = compute_tax_summary(alt_input)

            st.markdown("### Base vs Scenario B (key metrics)")

            col_m1, col_m2, col_m3 = st.columns(3)
            with col_m1:
                st.markdown("**Metric**")
                st.write("Filing status")
                st.write("Total income")
                st.write("Taxable income")
                st.write("Tax liability")
                st.write("Federal withholding")
                st.write("Refund")
                st.write("Amount owed")

            with col_m2:
                st.markdown("**Base case**")
                st.write(filing_status)
                st.write(f"${tax_summary['total_income']:,.2f}")
                st.write(f"${tax_summary['taxable_income']:,.2f}")
                st.write(f"${tax_liability:,.2f}")
                st.write(f"${tax_summary['federal_withholding']:,.2f}")
                st.write(f"${tax_summary['refund']:,.2f}")
                st.write(f"${tax_summary['amount_owed']:,.2f}")

            with col_m3:
                st.markdown("**Scenario B**")
                st.write(scenario_b_filing_status)
                st.write(f"${alt_summary['total_income']:,.2f}")
                st.write(f"${alt_summary['taxable_income']:,.2f}")
                st.write(
                    f"${alt_summary.get('tax_liability', alt_summary.get('tax_liility', 0.0)):,.2f}"
                )
                st.write(f"${alt_summary['federal_withholding']:,.2f}")
                st.write(f"${alt_summary['refund']:,.2f}")
                st.write(f"${alt_summary['amount_owed']:,.2f}")

            delta_refund = alt_summary["refund"] - tax_summary["refund"]
            delta_owed = alt_summary["amount_owed"] - tax_summary["amount_owed"]

            st.markdown(
                f"""
                <div style="line-height:1.8; font-size:1.05rem; padding-top:10px;">
                    <b>Change in refund:</b> ${delta_refund:,.2f}<br>
                    <b>Change in amount owed:</b> ${delta_owed:,.2f}
                </div>
                """,
                unsafe_allow_html=True,
            )
        else:
            st.info(
                "Set an income/withholding change or different filing status above to see Scenario B."
            )

        # ---------------------------
        # What-if analysis (sliders)
        # ---------------------------
        with st.expander("💡 What if my income or withholding changes (quick sliders)?"):
            st.write(
                "Move the sliders to see how **extra income** or **extra withholding** "
                "would change your refund or amount owed."
            )

            col_wi, col_wh = st.columns(2)
            with col_wi:
                delta_income = st.slider(
                    "Change in wages / income",
                    min_value=-50000,
                    max_value=50000,
                    value=0,
                    step=1000,
                    help="Negative = less income, positive = more income",
                )
            with col_wh:
                delta_withholding = st.slider(
                    "Change in federal withholding",
                    min_value=-20000,
                    max_value=20000,
                    value=0,
                    step=500,
                    help="Negative = less withheld, positive = more withheld",
                )

            if delta_income != 0 or delta_withholding != 0:
                hypothetical_input = TaxInput(
                    wages=total_wages + delta_income,
                    interest_income=total_interest,
                    nonemployee_income=total_nonemployee,
                    federal_withholding=total_withholding + delta_withholding,
                    filing_status=filing_status,
                    dependents=dependents,
                )
                hypot_summary = compute_tax_summary(hypothetical_input)

                col_h1, col_h2 = st.columns(2)
                with col_h1:
                    st.metric(
                        "New refund",
                        f"${hypot_summary['refund']:,.2f}",
                        delta=f"{hypot_summary['refund'] - tax_summary['refund']:,.2f}",
                    )
                with col_h2:
                    st.metric(
                        "New amount owed",
                        f"${hypot_summary['amount_owed']:,.2f}",
                        delta=f"{hypot_summary['amount_owed'] - tax_summary['amount_owed']:,.2f}",
                    )
            else:
                st.info("Adjust the sliders above to run a what-if scenario.")

        # ---------------------------
        # 12. 1040 PDF preview
        # ---------------------------
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

        pdf_buffer = generate_1040_pdf(personal_info, income_data, tax_summary)
        pdf_bytes = pdf_buffer.getvalue()
        b64_pdf = base64.b64encode(pdf_bytes).decode("utf-8")

        st.subheader("12. Preview & Download Draft Form 1040")

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

    # =======================
    # TAB 2: AI TAX ADVISOR
    # =======================
    with tab_ai_advisor:
        st.subheader("🧠 AI Tax Advisor (Groq) – Educational Only")

        st.info(
            "This tab uses a Groq LLM (Llama 3) to help explain your tax summary in plain language. "
            "It is **not** a CPA and **not** official tax advice."
        )

        default_question = (
            "Explain my current federal tax situation in simple terms, including why I "
            "have a refund or amount owed, and suggest common things people often review "
            "with a tax professional (like withholding, estimated payments, or SE tax)."
        )

        user_question = st.text_area(
            "Ask a question about your tax summary",
            value=default_question,
            height=140,
        )

        if st.button("Ask AI Tax Advisor", type="primary"):
            with st.spinner("Calling Groq AI Tax Advisor..."):
                ai_answer = ask_tax_advisor(
                    user_question=user_question,
                    tax_summary=tax_summary,
                    income_data=income_data,
                    filing_status=filing_status,
                )
                html_answer = f"""
                <div style="
                    max-width: 760px;
                    margin: 1.5rem auto;
                    padding: 1.5rem 1.75rem;
                    background-color: #ffffff;
                    border-radius: 12px;
                    box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
                    line-height: 1.6;
                    font-size: 0.95rem;
                ">
                {ai_answer}
                </div>
                """

                st.markdown(html_answer, unsafe_allow_html=True)


    # =======================
    # TAB 3: PARSING DETAILS
    # =======================
    with tab_details:
        if mode == "Use uploaded PDFs (real forms)" and file_results:
            st.subheader("Per-file Extraction Details (Validation on Original Forms)")
            for file_result in file_results:
                parser_label = file_result.get("parser_used", "rule-based")
                st.markdown(
                    f"**File:** `{file_result['filename']}`  |  "
                    f"Detected type: `{file_result['document_type']}`  |  "
                    f"Parser used: `{parser_label}`"
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
