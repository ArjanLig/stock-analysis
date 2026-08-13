"""Grading a DCF against the business it describes.

Two questions the app could not answer: how hard the assumption is, and
whether the company has since delivered it. Kept out of the page so the
arithmetic can be tested without a Streamlit runtime.
"""

# Underwriting more than this multiple of what a business has actually
# delivered is where a DCF stops being a forecast and becomes a bet on a break
# in trend. Below it, the model survives a bad year; above it, the fair value
# depends on the trend changing.
HEROIC_RATIO = 1.5


def _cagr(growth):
    """Compound the per-year growth path into a single rate."""
    compounded = 1.0
    for g in growth:
        compounded *= (1 + g)
    return compounded ** (1 / len(growth)) - 1


def thesis_vs_history(revenue_growth, delivered_cagr, years=5):
    """Compare the assumed growth path to the realised one, or None.

    Returns {"assumed_cagr", "delivered_cagr", "ratio", "heroic"}.

    `ratio` is None when the business has been shrinking: dividing by a
    negative CAGR gives a number whose sign means nothing. Assuming growth
    where there has been decline is still worth flagging, so `heroic` stands on
    its own.
    """
    if not revenue_growth or delivered_cagr is None:
        return None
    assumed = _cagr(list(revenue_growth)[:years])
    if delivered_cagr <= 0:
        return {"assumed_cagr": assumed, "delivered_cagr": delivered_cagr,
                "ratio": None, "heroic": assumed > 0}
    ratio = assumed / delivered_cagr
    return {"assumed_cagr": assumed, "delivered_cagr": delivered_cagr,
            "ratio": ratio, "heroic": ratio > HEROIC_RATIO}
