# ai_advisor.py

import os
from typing import Dict, Any

try:
    from groq import Groq
except ImportError:
    Groq = None  # We'll handle this gracefully


def _get_groq_client():
    """
    Return a Groq client if the GROQ_API_KEY is configured.
    Otherwise return None and let the caller handle it.
    """
    if Groq is None:
        return None, "groq-python library is not installed. Run: pip install groq"

    # Prefer environment variable for safety; fall back to hardcoded if you want.
    api_key = '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'
    # If you really want to hardcode, uncomment this and comment out the line above:
    # api_key = "YOUR_GROQ_KEY_HERE"

    if not api_key:
        return None, "GROQ_API_KEY environment variable is not set."

    try:
        client = Groq(api_key=api_key)
    except Exception as e:
        return None, f"Failed to initialize Groq client: {e}"

    return client, None


def build_tax_context(
    tax_summary: Dict[str, Any],
    income_data: Dict[str, float],
    filing_status: str,
) -> str:
    """
    Build a compact, model-friendly context string summarizing the user's situation.
    """
    wages = float(income_data.get("wages", 0.0))
    interest_income = float(income_data.get("interest_income", 0.0))
    nonemployee_income = float(income_data.get("nonemployee_income", 0.0))
    federal_withholding = float(income_data.get("federal_withholding", 0.0))

    total_income = float(tax_summary.get("total_income", 0.0))
    taxable_income = float(tax_summary.get("taxable_income", 0.0))
    tax_liability = float(tax_summary.get("tax_liability", 0.0))
    refund = float(tax_summary.get("refund", 0.0))
    amount_owed = float(tax_summary.get("amount_owed", 0.0))
    standard_deduction = float(tax_summary.get("standard_deduction", 0.0))

    se_net = float(tax_summary.get("se_net_earnings", 0.0))
    se_total = float(tax_summary.get("se_total_tax", 0.0))
    se_ded = float(tax_summary.get("se_deduction", 0.0))

    lines = [
        "USER TAX SNAPSHOT (for AI advisor; prototype, not real tax advice):",
        f"- Filing status: {filing_status}",
        f"- Wages (W-2): {wages:,.2f}",
        f"- Interest income (1099-INT): {interest_income:,.2f}",
        f"- Nonemployee income (1099-NEC): {nonemployee_income:,.2f}",
        f"- Federal withholding (W-2/1099): {federal_withholding:,.2f}",
        "",
        f"- Total income: {total_income:,.2f}",
        f"- Standard deduction: {standard_deduction:,.2f}",
        f"- Taxable income: {taxable_income:,.2f}",
        f"- Regular income tax (no credits): {tax_liability:,.2f}",
        f"- Calculated refund: {refund:,.2f}",
        f"- Calculated amount owed: {amount_owed:,.2f}",
    ]

    if nonemployee_income > 0:
        lines.extend(
            [
                "",
                "Self-employment tax prototype:",
                f"- Net earnings from self-employment (92.35% of NE income): {se_net:,.2f}",
                f"- Approx. self-employment tax (SS + Medicare): {se_total:,.2f}",
                f"- Deductible half of SE tax (Schedule 1-style): {se_ded:,.2f}",
            ]
        )

    return "\n".join(lines)

import re

def clean_llm_output(text: str) -> str:
    import re

    # Replace italics markers with spaces (avoid smashing words)
    text = text.replace("*", " ").replace("_", " ")

    # Space after punctuation ("400.Your" → "400. Your")
    text = re.sub(r'([.,!?])([A-Za-z])', r'\1 \2', text)

    # Remove "50, 000" → "50,000"
    text = re.sub(r'(\d),\s+(\d)', r'\1,\2', text)

    # Digit + letter stuck ("4092withheld" → "4092 withheld")
    text = re.sub(r'(\d)([A-Za-z])', r'\1 \2', text)

    # Fix glued lowercase words ("andafter" → "and after")
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)

    # Normalize list bullets
    text = re.sub(r'^\s*-\s+', '• ', text, flags=re.MULTILINE)

    # Fix excessive blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)

    # Normalize multiple spaces
    text = re.sub(r'\s{2,}', ' ', text)

    return text.strip()




def ask_tax_advisor(
    user_question: str,
    tax_summary: Dict[str, Any],
    income_data: Dict[str, float],
    filing_status: str,
    # New default model: current recommended general text model on Groq
    model: str = "llama-3.3-70b-versatile",
) -> str:
    """
    Calls Groq LLM as an 'AI tax advisor' (educational only).
    Returns a markdown-formatted string answer, or an error message.
    """
    client, err = _get_groq_client()
    if err:
        return (
            "⚠️ AI Tax Advisor is not available:\n\n"
            f"{err}\n\n"
            "Configure GROQ_API_KEY and install `groq` to enable this feature."
        )

    context = build_tax_context(tax_summary, income_data, filing_status)

    system_prompt = (
        "You are an educational tax explainer. You are NOT a CPA and NOT a tax advisor. "
        "You may only provide general educational information based on the user's summary. "
        "Do NOT give personalized tax advice. Always explicitly remind the user that this "
        "is not legal or tax advice and that they should consult a qualified professional "
        "for real filing decisions."
    )

    try:
        completion = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        context
                        + "\n\n---\n\n"
                        "USER QUESTION:\n"
                        + user_question
                    ),
                },
            ],
            temperature=0.4,
            max_tokens=800,
        )

        answer = completion.choices[0].message.content

        # Groq responses can be list-of-chunks or a string; normalize to string.
        if isinstance(answer, list):
            answer = " ".join(
                (part.get("text", "") if isinstance(part, dict) else str(part)).strip()
                for part in answer
                if part is not None
            )

        footer = (
            "\n\n---\n"
            "_Reminder: This is an educational prototype, not tax advice. "
            "For real decisions, talk to a qualified tax professional._"
        )
        final_text = clean_llm_output(answer) + footer
        return final_text


    except Exception as e:
        return f"⚠️ Error calling Groq AI Tax Advisor: {e}"
