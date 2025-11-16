# tax_calculator.py

from dataclasses import dataclass

@dataclass
class TaxInput:
    wages: float = 0.0
    interest_income: float = 0.0
    nonemployee_income: float = 0.0  # e.g., 1099-NEC
    federal_withholding: float = 0.0
    filing_status: str = "Single"
    dependents: int = 0


# -----------------------------------
# 1. Standard deduction (2024 approx)
# -----------------------------------
def get_standard_deduction(filing_status: str) -> float:
    """
    Approximate 2024 standard deductions (good enough for demo).
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
    # fallback (e.g., QSS)
    return 14600.0


# -----------------------------------
# 2. 2024 tax brackets (approx)
# -----------------------------------
def get_tax_brackets(filing_status: str):
    """
    Returns list of (upper_limit, rate) for marginal brackets.
    Numbers are approximate for 2024.
    """
    status = filing_status.lower()

    # SINGLE
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

    # MARRIED FILING JOINTLY
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

    # HEAD OF HOUSEHOLD
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

    # Default (also used for MFS / QSS here for simplicity)
    else:
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
    Apply progressive tax brackets to taxable income.
    """
    brackets = get_tax_brackets(filing_status)
    tax = 0.0
    previous_limit = 0.0

    for upper_limit, rate in brackets:
        if taxable_income <= previous_limit:
            break

        amount_in_bracket = min(taxable_income, upper_limit) - previous_limit
        if amount_in_bracket <= 0:
            continue

        tax += amount_in_bracket * rate
        previous_limit = upper_limit

        if taxable_income <= upper_limit:
            break

    return tax


# -----------------------------------
# 3. Self-Employment Tax (Schedule SE prototype)
# -----------------------------------
def compute_self_employment_tax(nonemployee_income: float) -> dict:
    """
    Compute self-employment (SE) tax components for 1099-NEC income.

    - Net earnings = 92.35% of gross SE income
    - Social Security part = 12.4% of net earnings up to wage base
    - Medicare part = 2.9% of all net earnings
    - Total SE tax = SS + Medicare
    - Deductible part = 1/2 of SE tax (above-the-line adjustment on Schedule 1)
    """
    se_gross = max(0.0, nonemployee_income)

    if se_gross <= 0.0:
        return {
            "se_gross": 0.0,
            "se_net_earnings": 0.0,
            "se_ss_tax": 0.0,
            "se_medicare_tax": 0.0,
            "se_total_tax": 0.0,
            "se_deduction": 0.0,
        }

    # 92.35% of SE income is subject to SE tax
    se_net = se_gross * 0.9235

    # 2024 Social Security wage base (approx; good enough for demo)
    SS_WAGE_BASE_2024 = 168600.0

    # Social Security part: 12.4% up to wage base
    ss_base = min(se_net, SS_WAGE_BASE_2024)
    se_ss_tax = ss_base * 0.124

    # Medicare part: 2.9% on all net SE earnings
    se_medicare_tax = se_net * 0.029

    # Total SE tax
    se_total_tax = se_ss_tax + se_medicare_tax

    # Deductible half of SE tax (Schedule 1 adjustment)
    se_deduction = se_total_tax * 0.5

    return {
        "se_gross": se_gross,
        "se_net_earnings": se_net,
        "se_ss_tax": se_ss_tax,
        "se_medicare_tax": se_medicare_tax,
        "se_total_tax": se_total_tax,
        "se_deduction": se_deduction,
    }


# -----------------------------------
# 4. Top-level tax summary
# -----------------------------------
def compute_tax_summary(tax_input: TaxInput) -> dict:
    """
    Core tax engine used by the Streamlit app.

    - Aggregates W-2 wages, 1099-INT interest, 1099-NEC nonemployee income
    - Computes self-employment tax & its deductible half
    - Applies:
        total income
        -> (minus SE deduction) = AGI
        -> (minus standard deduction) = taxable income
    - Computes regular federal income tax using 2024 brackets
    - Adds SE tax on top to get total federal tax liability
    - Compares total tax to withholding to get refund / amount owed
    """

    # 1) Aggregate income (this corresponds to "total income" on 1040)
    total_income = (
        tax_input.wages
        + tax_input.interest_income
        + tax_input.nonemployee_income
    )

    # 2) Self-employment tax components (for 1099-NEC)
    se_components = compute_self_employment_tax(tax_input.nonemployee_income)
    se_deduction = se_components["se_deduction"]  # above-the-line adjustment

    # 3) Adjusted Gross Income (AGI) after SE deduction
    agi = max(0.0, total_income - se_deduction)

    # 4) Standard deduction and taxable income
    standard_deduction = get_standard_deduction(tax_input.filing_status)
    taxable_income = max(0.0, agi - standard_deduction)

    # 5) Regular federal income tax (on taxable income only)
    income_tax = compute_marginal_tax(taxable_income, tax_input.filing_status)

    # 6) Total federal tax = income tax + SE tax (like 1040 + Schedule 2)
    se_tax = se_components["se_total_tax"]
    total_tax = income_tax + se_tax

    # 7) Compare total tax to withholding
    federal_withheld = tax_input.federal_withholding
    refund = max(0.0, federal_withheld - total_tax)
    amount_owed = max(0.0, total_tax - federal_withheld)

    summary = {
        # Core income pipeline
        "total_income": total_income,
        "se_deduction": se_deduction,   # Schedule 1 adjustment
        "agi": agi,                     # Adjusted Gross Income
        "standard_deduction": standard_deduction,
        "taxable_income": taxable_income,

        # Tax components
        "income_tax": income_tax,       # Bracket-based income tax only
        "se_tax": se_tax,               # Self-employment tax
        "tax_liability": total_tax,     # Total federal tax (income + SE)

        # Payments vs liability
        "federal_withholding": federal_withheld,
        "refund": refund,
        "amount_owed": amount_owed,
    }

    # Attach detailed SE fields so app.py can display them
    summary.update(se_components)

    return summary
