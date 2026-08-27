import unittest

from fastfunds.core import (
    annualized_return,
    calculate_cagr,
    cagr_details,
    fair_value_multiple,
    normalize_ticker,
    valuation_summary,
)


class CoreCalculationTests(unittest.TestCase):
    def test_ticker_normalization_preserves_share_class(self):
        self.assertEqual(normalize_ticker("brk.b.us"), "BRK-B")
        self.assertEqual(normalize_ticker(" BF-B "), "BF-B")
        self.assertEqual(normalize_ticker("mc.pa"), "MC.PA")

    def test_fair_value_multiple_uses_cagr_and_weighted_selected_years(self):
        self.assertEqual(fair_value_multiple(10, 5), 15)
        self.assertAlmostEqual(fair_value_multiple(20, 5), 19.474, places=3)
        self.assertAlmostEqual(fair_value_multiple(0, 5), 11.270, places=3)
        self.assertAlmostEqual(fair_value_multiple(20, 20), 42.614, places=3)
        self.assertEqual(fair_value_multiple(30, 20), 60)
        self.assertIsNone(fair_value_multiple(10, None))
        self.assertIsNone(fair_value_multiple(10, 0))

    def test_cagr_uses_exact_dates_and_positive_endpoints(self):
        value = calculate_cagr(
            [
                {"date": "2020-01-01", "value": 1},
                {"date": "2025-01-01", "value": 2},
            ]
        )
        self.assertAlmostEqual(value, 14.87, places=1)
        self.assertIsNone(calculate_cagr([{"date": "2020-01-01", "value": -1}]))
        details = cagr_details(
            [
                {"date": "2020-01-01", "value": 1},
                {"date": "2025-01-01", "value": 2},
            ]
        )
        self.assertAlmostEqual(details["years"], 5, places=2)
        self.assertEqual(details["startDate"], "2020-01-01")
        self.assertEqual(details["endDate"], "2025-01-01")

    def test_valuation_uses_formula_multiple_without_split_prices(self):
        fundamentals = [
            {"period_end": "2020-12-31", "value": 1},
            {"period_end": "2021-12-31", "value": 1.1},
            {"period_end": "2022-12-31", "value": 1.2},
            {"period_end": "2023-12-31", "value": 1.3},
            {"period_end": "2024-12-31", "value": 1.4},
        ]
        result = valuation_summary(fundamentals, custom_multiple=12)
        formula_result = valuation_summary(fundamentals)
        capped_custom_result = valuation_summary(fundamentals, custom_multiple=120)
        self.assertEqual(result["formulaVersion"], "cagr-duration-weighted-pe-capped-60-v2")
        self.assertEqual(result["maximumMultiple"], 60)
        self.assertEqual(result["durationWeight"], 0.6)
        self.assertEqual(result["appliedMultiple"], 12)
        self.assertEqual(capped_custom_result["appliedMultiple"], 60)
        self.assertAlmostEqual(result["cagrYears"], 4, places=2)
        self.assertAlmostEqual(result["formulaMultiple"], 14.60, places=2)
        self.assertAlmostEqual(result["valuationPoints"][-1]["fairValue"], 16.8)
        self.assertAlmostEqual(
            formula_result["valuationPoints"][-1]["fairValue"],
            fundamentals[-1]["value"] * formula_result["formulaMultiple"],
        )

    def test_annualized_return(self):
        result = annualized_return(100, 121, "2020-01-01", "2022-01-01")
        self.assertAlmostEqual(result["totalPercent"], 21)
        self.assertAlmostEqual(result["annualizedPercent"], 10, places=1)


if __name__ == "__main__":
    unittest.main()
