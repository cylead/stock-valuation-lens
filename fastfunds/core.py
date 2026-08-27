"""Pure calculation helpers shared by the importer, API, and tests."""

from __future__ import division

import datetime as _dt
import math


METRICS = {
    "eps_diluted": {
        "column": "diluted_eps",
        "label": "Diluted EPS",
        "source_column": "diluted_source",
    },
    "eps_basic": {
        "column": "basic_eps",
        "label": "Basic EPS",
        "source_column": "basic_source",
    },
    "fcf_per_share": {
        "column": "fcf_per_share",
        "label": "Free cash flow / share",
        "source_column": "fcf_source",
    },
}

MAXIMUM_PE_MULTIPLE = 60.0
DURATION_WEIGHT = 0.6


def parse_date(value):
    """Parse an ISO date and raise ValueError for invalid input."""
    return _dt.datetime.strptime(value, "%Y-%m-%d").date()


def normalize_ticker(value):
    """Normalize supported ticker spellings without conflating share classes.

    SEC and Stooq use a period for US share classes (``BRK.B``), while the
    application also accepts exchange-qualified symbols such as ``MC.PA``.
    Keep recognised exchange suffixes intact so they remain searchable and
    visible using their conventional market notation.
    """
    ticker = (value or "").strip().upper()
    if ticker.endswith(".US"):
        ticker = ticker[:-3]
    elif ticker.rsplit(".", 1)[-1] in {
        "PA", "AS", "BR", "CO", "DE", "HE", "HK", "L", "MI", "OL", "ST", "SW", "TO", "VI",
    }:
        return ticker
    return ticker.replace(".", "-")


def annual_duration(start, end):
    """Return duration in days, or None if either date is invalid."""
    try:
        return (parse_date(end) - parse_date(start)).days
    except (TypeError, ValueError):
        return None


def cagr_details(points):
    """Return CAGR inputs calculated from positive annual observations.

    ``points`` is an iterable of dictionaries with ``date`` and ``value``.
    Non-positive and missing observations are ignored. Exact fiscal-end dates
    determine the elapsed years, so the CAGR and its duration always describe
    the same selected observations.
    """
    valid = []
    for point in points:
        value = point.get("value")
        if value is None or value <= 0:
            continue
        try:
            date = parse_date(point["date"])
        except (KeyError, TypeError, ValueError):
            continue
        valid.append((date, float(value)))
    valid.sort(key=lambda item: item[0])
    if len(valid) < 2:
        return None
    first_date, first_value = valid[0]
    last_date, last_value = valid[-1]
    years = (last_date - first_date).days / 365.2425
    if years <= 0:
        return None
    return {
        "cagr": (math.pow(last_value / first_value, 1.0 / years) - 1.0) * 100.0,
        "years": years,
        "startDate": first_date.isoformat(),
        "endDate": last_date.isoformat(),
    }


def calculate_cagr(points):
    """Calculate endpoint CAGR for positive annual observations.

    This compatibility helper returns only the CAGR. Use ``cagr_details``
    when the matching duration or fiscal endpoints are also needed.
    """
    details = cagr_details(points)
    return details["cagr"] if details else None


def fair_value_multiple(growth_percent, years):
    """Calculate a P/E capped at 60x from CAGR and weighted duration.

    ``growth_percent`` is expressed as a percentage (for example, ``10`` for
    10%) and ``years`` is the exact elapsed duration of the selected annual
    observations. The duration is weighted at ``DURATION_WEIGHT`` to give a
    long historical growth run only partial credit. The returned P/E is then
    multiplied by each positive per-share metric to draw the formula reference
    value. Long windows cannot produce a P/E above ``MAXIMUM_PE_MULTIPLE``.
    """
    if growth_percent is None or years is None:
        return None
    growth = float(growth_percent)
    duration = float(years)
    if not math.isfinite(growth) or not math.isfinite(duration) or duration <= 0:
        return None
    multiple = 15.0 * math.pow(
        (1.0 + growth / 100.0) / 1.10,
        duration * DURATION_WEIGHT,
    )
    return min(MAXIMUM_PE_MULTIPLE, multiple)


def valuation_summary(fundamentals, custom_multiple=None):
    """Calculate window-dependent CAGR and formula valuation data."""
    metric_points = [
        {"date": row["period_end"], "value": row.get("value")}
        for row in fundamentals
    ]
    cagr_input = cagr_details(metric_points)
    cagr = cagr_input["cagr"] if cagr_input else None
    cagr_years = cagr_input["years"] if cagr_input else None
    formula_multiple = fair_value_multiple(cagr, cagr_years)
    applied_multiple = formula_multiple
    if custom_multiple is not None:
        try:
            candidate = float(custom_multiple)
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and math.isfinite(candidate) and candidate > 0:
            applied_multiple = min(MAXIMUM_PE_MULTIPLE, candidate)

    valuations = []
    for row in sorted(fundamentals, key=lambda item: item["period_end"]):
        value = row.get("value")
        fair_value = None
        if value is not None and value > 0:
            if applied_multiple is not None:
                fair_value = float(value) * applied_multiple
        valuations.append(
            {
                "date": row["period_end"],
                "metricValue": value,
                "fairValue": fair_value,
            }
        )

    return {
        "formulaVersion": "cagr-duration-weighted-pe-capped-60-v2",
        "maximumMultiple": MAXIMUM_PE_MULTIPLE,
        "durationWeight": DURATION_WEIGHT,
        "cagr": cagr,
        "cagrYears": cagr_years,
        "cagrStartDate": cagr_input["startDate"] if cagr_input else None,
        "cagrEndDate": cagr_input["endDate"] if cagr_input else None,
        "formulaMultiple": formula_multiple,
        "appliedMultiple": applied_multiple,
        "valuationPoints": valuations,
    }


def annualized_return(start_value, end_value, start_date, end_date):
    """Calculate total and annualized return for two positive observations."""
    if not start_value or not end_value or start_value <= 0 or end_value <= 0:
        return None
    start = parse_date(start_date)
    end = parse_date(end_date)
    years = (end - start).days / 365.2425
    if years <= 0:
        return None
    total = (float(end_value) / float(start_value) - 1.0) * 100.0
    annualized = (math.pow(float(end_value) / float(start_value), 1.0 / years) - 1.0) * 100.0
    return {"totalPercent": total, "annualizedPercent": annualized, "years": years}
