# tax_calculator.py

from dataclasses import dataclass

@dataclass
class TaxInput:
    wages: float = 0.0
    interest_income: float = 0.0
    nonemployee_income: float = 0.0
    federal_withholding: float = 0.0
    filing_status: str = "Single"
    dependents: int = 0


def get_standard_deduction(filing_status: str) -> float:
    """
    Approximate 2024 standard deductions. Adjust if you want exact IRS figures.
    """
    status = filing_status.lower()
    if status == "single":
        return 14600.0
    elif status == "married filing jointly":
        return 29200.0
    elif status == "married filing separately":
        return 14600.0
    elif status == "head of household":
        return 21900.0
    # fallback
    return 14600.0


def get_tax_brackets(filing_status: str):
    """
    Returns a list of (upper_limit, rate) for marginal brackets.
    Numbers are approx for 2024; good enough for demo.
    """
    status = filing_status.lower()

    # These are sample brackets for SINGLE.
    if status == "single":
        return [
            (11600, 0.10),
            (47150, 0.12),
            (100525, 0.22),
            (191950, 0.24),
            (243725, 0.32),
            (609350, 0.35),
            (float("inf"), 0.37),
        ]
    elif status == "married filing jointly":
        return [
            (23200, 0.10),
            (94300, 0.12),
            (201050, 0.22),
            (383900, 0.24),
            (487450, 0.32),
            (731200, 0.35),
            (float("inf"), 0.37),
        ]
    elif status == "head of household":
        return [
            (16550, 0.10),
            (63100, 0.12),
            (100500, 0.22),
            (191950, 0.24),
            (243700, 0.32),
            (609350, 0.35),
            (float("inf"), 0.37),
        ]
    else:  # default to single
        return [
            (11600, 0.10),
            (47150, 0.12),
            (100525, 0.22),
            (191950, 0.24),
            (243725, 0.32),
            (609350, 0.35),
            (float("inf"), 0.37),
        ]


def compute_marginal_tax(taxable_income: float, filing_status: str) -> float:
    """
    Apply progressive tax to taxable income.
    """
    brackets = get_tax_brackets(filing_status)
    tax = 0.0
    previous_limit = 0.0

    for upper_limit, rate in brackets:
        if taxable_income <= previous_limit:
            break

        amount_in_bracket = min(taxable_income, upper_limit) - previous_limit
        tax += amount_in_bracket * rate
        previous_limit = upper_limit

        if taxable_income <= upper_limit:
            break

    return tax


def compute_tax_summary(tax_input: TaxInput) -> dict:
    total_income = tax_input.wages + tax_input.interest_income + tax_input.nonemployee_income

    standard_deduction = get_standard_deduction(tax_input.filing_status)
    taxable_income = max(0.0, total_income - standard_deduction)

    tax_liability = compute_marginal_tax(taxable_income, tax_input.filing_status)

    federal_withheld = tax_input.federal_withholding
    refund = max(0.0, federal_withheld - tax_liability)
    amount_owed = max(0.0, tax_liability - federal_withheld)

    return {
        "total_income": total_income,
        "standard_deduction": standard_deduction,
        "taxable_income": taxable_income,
        "tax_liability": tax_liability,
        "federal_withholding": federal_withheld,
        "refund": refund,
        "amount_owed": amount_owed,
    }
