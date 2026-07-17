# test_gather_data_investments.py
"""fetch_fundamentals must carry short/long-term investment series so the
ROCE excess-liquidity helper can read them."""

from unittest.mock import patch

import gather_data


def _facts():
    """Minimal SEC companyfacts payload: one us-gaap fact per tag, FY2023."""
    def unit(val):
        return {"units": {"USD": [
            {"end": "2023-12-31", "val": val, "fy": 2023, "fp": "FY", "form": "10-K"},
        ]}}
    return {"facts": {"us-gaap": {
        # A revenue tag is required so parse_financials() succeeds without
        # falling back to the yfinance income-statement fetch (which would
        # hit the network for a fake ticker in this offline test).
        "RevenueFromContractWithCustomerExcludingAssessedTax": unit(900_000_000),
        "Assets": unit(1_000_000_000),
        "LiabilitiesCurrent": unit(200_000_000),
        "OperatingIncomeLoss": unit(150_000_000),
        "ShortTermInvestments": unit(80_000_000),
        "LongTermInvestments": unit(120_000_000),
        "CashAndCashEquivalentsAtCarryingValue": unit(50_000_000),
    }}}


def test_fetch_fundamentals_carries_investment_series():
    with patch("gather_data.get_cik", return_value="0000000001"), \
         patch("gather_data.fetch_company_facts", return_value=_facts()):
        fund = gather_data.fetch_fundamentals("TEST", n_years=5)
    assert "short_term_investments" in fund
    assert "long_term_investments" in fund
    # values are in millions (EDGAR value / 1e6)
    assert fund["short_term_investments"][-1] == 80.0
    assert fund["long_term_investments"][-1] == 120.0


def test_investment_fields_are_overridable():
    assert "short_term_investments" in gather_data.OVERRIDABLE_FUNDAMENTALS_FIELDS
    assert "long_term_investments" in gather_data.OVERRIDABLE_FUNDAMENTALS_FIELDS
