import csv
import json
import os
import sqlite3
import tempfile
import unittest

from fastfunds.importer import build_database, discover_stooq_stocks, extract_fundamentals
from fastfunds.server import _annual_dividend_yield_series, build_chart_payload, search_companies


def annual_fact(start, end, value, filed, accession="0000000000-24-000001"):
    return {
        "start": start,
        "end": end,
        "val": value,
        "accn": accession,
        "fy": int(end[:4]),
        "fp": "FY",
        "form": "10-K",
        "filed": filed,
    }


class AnnualDividendYieldTests(unittest.TestCase):
    def test_annual_yields_prefer_recent_prior_split_prices_and_keep_unavailable_years(self):
        dividends = [
            {"date": "2022-12-31", "fiscalYear": 2022, "value": 1.0, "source": {"label": "FY22"}},
            {"date": "2023-12-31", "fiscalYear": 2023, "value": 1.0, "source": {"label": "FY23"}},
            {"date": "2024-12-31", "fiscalYear": 2024, "value": 2.0, "source": {"label": "FY24"}},
            {"date": "2025-12-31", "fiscalYear": 2025, "value": 0.0, "source": {"label": "FY25"}},
            {"date": "2026-12-31", "fiscalYear": 2026, "value": -1.0, "source": {"label": "FY26"}},
            {"date": "2027-12-31", "fiscalYear": 2027, "value": 3.0, "source": {"label": "FY27"}},
            {"date": "2028-12-31", "fiscalYear": 2028, "value": 4.0, "source": {"label": "FY28"}},
            {"date": "2029-12-31", "fiscalYear": 2029, "value": None, "source": {"label": "FY29"}},
        ]
        prices = [
            # This is after FY22 and must never be used for that year's yield.
            {"date": "2023-01-02", "splitClose": 10.0, "adjustedClose": 10.0},
            {"date": "2023-12-29", "splitClose": 50.0, "adjustedClose": 40.0},
            # The split-only price is stale, so the recent adjusted close is used.
            {"date": "2024-12-01", "splitClose": 50.0, "adjustedClose": 50.0},
            {"date": "2024-12-30", "splitClose": None, "adjustedClose": 100.0},
            {"date": "2025-12-30", "splitClose": 20.0, "adjustedClose": 20.0},
            {"date": "2026-12-30", "splitClose": 20.0, "adjustedClose": 20.0},
            # An invalid split price falls back to the adjusted close.
            {"date": "2028-12-30", "splitClose": 0.0, "adjustedClose": 80.0},
            {"date": "2029-12-31", "splitClose": 80.0, "adjustedClose": 80.0},
        ]

        result = _annual_dividend_yield_series(dividends, prices)

        self.assertEqual(len(result), len(dividends))
        self.assertIsNone(result[0]["value"])
        self.assertIsNone(result[0]["priceDate"])
        self.assertAlmostEqual(result[1]["value"], 2.0)
        self.assertEqual(result[1]["priceType"], "split_only_close")
        self.assertEqual(result[1]["priceDate"], "2023-12-29")
        self.assertAlmostEqual(result[2]["value"], 2.0)
        self.assertEqual(result[2]["priceType"], "stooq_adjusted_close")
        self.assertEqual(result[2]["priceDate"], "2024-12-30")
        self.assertEqual(result[3]["value"], 0.0)
        self.assertIsNone(result[4]["value"])
        self.assertIsNone(result[5]["value"])
        self.assertIsNone(result[5]["priceDate"])
        self.assertAlmostEqual(result[6]["value"], 5.0)
        self.assertEqual(result[6]["priceType"], "stooq_adjusted_close")
        self.assertIsNone(result[7]["value"])


class ImporterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = self.temp.name
        self.sec = os.path.join(self.root, "companyfacts")
        self.stooq = os.path.join(self.root, "daily")
        os.makedirs(self.sec)
        stock_dir = os.path.join(self.stooq, "us", "nasdaq stocks", "1")
        etf_dir = os.path.join(self.stooq, "us", "nasdaq etfs")
        os.makedirs(stock_dir)
        os.makedirs(etf_dir)
        self.stock_path = os.path.join(stock_dir, "test.us.txt")
        self.etf_path = os.path.join(etf_dir, "spy.us.txt")
        rows = [
            ["<TICKER>", "<PER>", "<DATE>", "<TIME>", "<OPEN>", "<HIGH>", "<LOW>", "<CLOSE>", "<VOL>", "<OPENINT>"],
            ["TEST.US", "D", "20221230", "000000", "7", "9", "7", "8", "100", "0"],
            ["TEST.US", "D", "20230103", "000000", "9", "11", "9", "10", "100", "0"],
            ["TEST.US", "D", "20230106", "000000", "10", "12", "10", "11", "100", "0"],
            ["TEST.US", "D", "20230109", "000000", "11", "13", "11", "12", "100", "0"],
            ["TEST.US", "D", "20240102", "000000", "19", "21", "19", "20", "100", "0"],
            ["TEST.US", "D", "20250102", "000000", "24", "26", "24", "25", "100", "0"],
        ]
        for path in (self.stock_path, self.etf_path):
            with open(path, "w", newline="", encoding="utf-8") as handle:
                csv.writer(handle).writerows(rows)

        self.crosswalk = os.path.join(self.root, "tickers.json")
        with open(self.crosswalk, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "fields": ["cik", "name", "ticker", "exchange"],
                    "data": [[1, "Test Corporation", "TEST", "Nasdaq"]],
                },
                handle,
            )
        facts = self.fixture_facts()
        with open(os.path.join(self.sec, "CIK0000000001.json"), "w", encoding="utf-8") as handle:
            json.dump(facts, handle)

        self.split = os.path.join(self.root, "split.csv")
        with open(self.split, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["ticker", "date", "close"])
            writer.writeheader()
            writer.writerows(
                [
                    {"ticker": "TEST", "date": "2022-12-30", "close": 8},
                    {"ticker": "TEST", "date": "2023-01-03", "close": 10},
                    {"ticker": "TEST", "date": "2023-01-06", "close": 11},
                    {"ticker": "TEST", "date": "2023-12-29", "close": 20},
                    {"ticker": "TEST", "date": "2024-12-30", "close": 25},
                ]
            )

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def fixture_facts():
        periods = [
            ("2022-01-01", "2022-12-31"),
            ("2023-01-01", "2023-12-31"),
            ("2024-01-01", "2024-12-31"),
        ]
        basic = [annual_fact(start, end, value, end[:4] + "-03-01") for (start, end), value in zip(periods, [1.0, 1.2, 1.5])]
        basic.append(annual_fact("2022-01-01", "2022-12-31", 1.1, "2024-03-01", "0000000000-24-000002"))
        diluted = [annual_fact(start, end, value, end[:4] + "-03-01") for (start, end), value in zip(periods, [0.9, 1.1, 1.4])]
        dividends = [annual_fact(start, end, value, end[:4] + "-03-01") for (start, end), value in zip(periods, [0.2, 0.24, 0.3])]
        ocf = [annual_fact(start, end, value, end[:4] + "-03-01") for (start, end), value in zip(periods, [100, 120, 150])]
        capex = [annual_fact(start, end, value, end[:4] + "-03-01") for (start, end), value in zip(periods, [20, 25, 30])]
        shares = [annual_fact(start, end, 10, end[:4] + "-03-01") for start, end in periods]
        return {
            "cik": 1,
            "entityName": "Test Corporation",
            "facts": {
                "us-gaap": {
                    "EarningsPerShareBasic": {"units": {"USD/shares": basic}},
                    "EarningsPerShareDiluted": {"units": {"USD/shares": diluted}},
                    "CommonStockDividendsPerShareDeclared": {"units": {"USD/shares": dividends}},
                    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
                    "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": capex}},
                    "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": shares}},
                }
            },
        }

    def test_etf_directories_are_not_discovered(self):
        records = discover_stooq_stocks(self.stooq)
        self.assertEqual(set(records), {"TEST"})

    def test_latest_restatement_is_retained(self):
        audit = {"summary": {"duplicateFactsResolved": 0}}
        rows = extract_fundamentals(self.fixture_facts(), audit)
        first = next(row for row in rows if row["period_end"] == "2022-12-31")
        self.assertEqual(first["basic_eps"], 1.1)
        self.assertEqual(first["dividend_per_share"], 0.2)
        self.assertGreater(audit["summary"]["duplicateFactsResolved"], 0)

    def test_per_share_facts_are_retroactively_split_adjusted_once(self):
        payload = {
            "facts": {
                "us-gaap": {
                    "EarningsPerShareDiluted": {
                        "units": {
                            "USD/shares": [
                                annual_fact("2021-01-01", "2021-12-31", 4, "2022-03-01"),
                                annual_fact("2022-01-01", "2022-12-31", 2.5, "2024-03-01"),
                            ]
                        }
                    },
                    "CommonStockDividendsPerShareDeclared": {
                        "units": {
                            "USD/shares": [
                                annual_fact("2021-01-01", "2021-12-31", 1, "2022-03-01"),
                            ]
                        }
                    },
                    "StockholdersEquityNoteStockSplitConversionRatio1": {
                        "units": {
                            "pure": [
                                {"end": "2023-06-01", "val": 2, "filed": "2023-08-01", "accn": "split"}
                            ]
                        }
                    },
                }
            }
        }
        audit = {"summary": {"duplicateFactsResolved": 0, "splitAdjustmentsApplied": 0}}
        rows = extract_fundamentals(payload, audit)
        self.assertEqual(rows[0]["diluted_eps"], 2)
        self.assertEqual(rows[0]["dividend_per_share"], 0.5)
        self.assertEqual(rows[1]["diluted_eps"], 2.5)
        source = json.loads(rows[0]["diluted_source"])
        self.assertEqual(source["reportedValue"], 4)
        self.assertEqual(source["splitAdjustments"][0]["ratio"], 2)
        self.assertEqual(audit["summary"]["splitAdjustmentsApplied"], 2)

    def test_cash_paid_dividend_is_used_when_declared_fact_is_unavailable(self):
        payload = {
            "facts": {
                "us-gaap": {
                    "CommonStockDividendsPerShareCashPaid": {
                        "units": {
                            "USD/shares": [
                                annual_fact("2024-01-01", "2024-12-31", 0.4, "2025-03-01"),
                            ]
                        }
                    }
                }
            }
        }
        audit = {"summary": {"duplicateFactsResolved": 0, "splitAdjustmentsApplied": 0}}
        rows = extract_fundamentals(payload, audit)
        self.assertEqual(rows[0]["dividend_per_share"], 0.4)
        source = json.loads(rows[0]["dividend_source"])
        self.assertEqual(source["tag"], "CommonStockDividendsPerShareCashPaid")

    def test_full_build_and_chart_payload(self):
        database = os.path.join(self.root, "app.sqlite3")
        audit_path = os.path.join(self.root, "audit.json")
        with open(self.stock_path, "rb") as handle:
            raw_before = handle.read()
        audit = build_database(
            self.sec,
            self.stooq,
            self.crosswalk,
            database,
            audit_path,
            split_prices_path=self.split,
        )
        self.assertEqual(audit["summary"]["matchedCompanies"], 1)
        self.assertEqual(audit["summary"]["companiesWithDividendPerShare"], 1)
        self.assertEqual(audit["summary"]["companiesWithFcfPerShare"], 1)
        self.assertEqual(audit["summary"]["companiesWithSplitPrices"], 1)
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            company = connection.execute("SELECT * FROM companies WHERE ticker='TEST'").fetchone()
            self.assertTrue(company["has_diluted_eps"])
            self.assertTrue(company["has_dividend_per_share"])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM price_weekly").fetchone()[0], 5)
            payload = build_chart_payload(connection, "TEST", "fcf_per_share")
            self.assertEqual(payload["company"]["ticker"], "TEST")
            self.assertEqual(len(payload["fundamentals"]), 3)
            self.assertEqual([row["value"] for row in payload["dividendSeries"]], [0.2, 0.24, 0.3])
            yields = payload["dividendYieldSeries"]
            self.assertEqual([row["date"] for row in yields], [
                "2022-12-31", "2023-12-31", "2024-12-31",
            ])
            self.assertAlmostEqual(yields[0]["value"], 2.5)
            self.assertAlmostEqual(yields[1]["value"], 1.2)
            self.assertAlmostEqual(yields[2]["value"], 1.2)
            self.assertEqual(yields[-1]["dividendPerShare"], 0.3)
            self.assertEqual(yields[-1]["periodEnd"], "2024-12-31")
            self.assertEqual(yields[-1]["priceDate"], "2024-12-30")
            self.assertEqual(yields[-1]["priceType"], "split_only_close")
            ranged = build_chart_payload(
                connection, "TEST", "fcf_per_share", start="2024-01-01", end="2024-12-31"
            )
            self.assertEqual(len(ranged["dividendYieldSeries"]), 1)
            self.assertAlmostEqual(ranged["dividendYieldSeries"][0]["value"], 1.2)
            self.assertEqual(ranged["dividendYieldSeries"][0]["priceDate"], "2024-12-30")
            self.assertTrue(payload["company"]["availability"]["dividend_per_share"])
            self.assertIsNotNone(payload["valuation"]["formulaMultiple"])
            results = search_companies(connection, "Test")
            self.assertEqual(results[0]["ticker"], "TEST")
        with open(self.stock_path, "rb") as handle:
            self.assertEqual(handle.read(), raw_before)

    def test_malformed_facts_are_audited_and_company_remains_searchable(self):
        bad_price = os.path.join(os.path.dirname(self.stock_path), "bad.us.txt")
        with open(self.stock_path, "rb") as source, open(bad_price, "wb") as destination:
            destination.write(source.read().replace(b"TEST.US", b"BAD.US"))
        with open(self.crosswalk, "r", encoding="utf-8") as handle:
            crosswalk = json.load(handle)
        crosswalk["data"].append([2, "Bad Facts Inc.", "BAD", "Nasdaq"])
        with open(self.crosswalk, "w", encoding="utf-8") as handle:
            json.dump(crosswalk, handle)
        with open(os.path.join(self.sec, "CIK0000000002.json"), "w", encoding="utf-8") as handle:
            handle.write("{not valid json")

        database = os.path.join(self.root, "malformed.sqlite3")
        audit = build_database(
            self.sec,
            self.stooq,
            self.crosswalk,
            database,
            os.path.join(self.root, "malformed_audit.json"),
        )
        self.assertEqual(audit["summary"]["malformedFiles"], 1)
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            results = search_companies(connection, "BAD")
            self.assertEqual(results[0]["ticker"], "BAD")
            self.assertFalse(results[0]["availability"]["eps_diluted"])

    def test_supplemental_company_uses_local_prices_and_reported_facts(self):
        supplemental_prices = os.path.join(self.root, "mc.pa.csv")
        with open(supplemental_prices, "w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "close"])
            writer.writerows(
                [
                    ["2023-12-29", "700.00"],
                    ["2024-12-30", "650.00"],
                    ["2025-12-30", "600.00"],
                ]
            )
        supplemental = os.path.join(self.root, "companies.json")
        with open(supplemental, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "companies": [
                        {
                            "ticker": "MC.PA",
                            "entityId": -1,
                            "name": "LVMH",
                            "exchange": "Euronext Paris",
                            "currency": "EUR",
                            "priceFile": "mc.pa.csv",
                            "priceSource": "Test close source",
                            "reportingSource": "Test annual report",
                            "fundamentals": [
                                {
                                    "periodEnd": "2023-12-31",
                                    "basicEps": 20.0,
                                    "dividendPerShare": 13.0,
                                    "source": {"label": "Test report"},
                                },
                                {
                                    "periodEnd": "2024-12-31",
                                    "basicEps": 25.0,
                                    "dividendPerShare": 14.0,
                                    "source": {"label": "Test report"},
                                },
                            ],
                        }
                    ]
                },
                handle,
            )
        database = os.path.join(self.root, "supplemental.sqlite3")
        audit = build_database(
            self.sec,
            self.stooq,
            self.crosswalk,
            database,
            os.path.join(self.root, "supplemental_audit.json"),
            supplemental_path=supplemental,
            tickers=["MC.PA"],
        )
        self.assertEqual(audit["summary"]["supplementalCompanies"], 1)
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            company = connection.execute("SELECT * FROM companies WHERE ticker='MC.PA'").fetchone()
            self.assertEqual(company["currency"], "EUR")
            self.assertFalse(company["is_sec_filer"])
            self.assertTrue(company["has_basic_eps"])
            self.assertTrue(company["has_dividend_per_share"])
            self.assertTrue(company["has_split_price"])
            payload = build_chart_payload(connection, "MC.PA", "eps_basic")
            self.assertEqual(len(payload["fundamentals"]), 2)
            self.assertEqual(payload["fundamentals"][-1]["value"], 25.0)
            self.assertEqual(payload["dividendSeries"][-1]["value"], 14.0)
            self.assertAlmostEqual(payload["dividendYieldSeries"][0]["value"], 13.0 / 700.0 * 100.0)
            self.assertAlmostEqual(payload["dividendYieldSeries"][-1]["value"], 14.0 / 650.0 * 100.0)
            self.assertEqual(payload["dividendYieldSeries"][-1]["priceDate"], "2024-12-30")
            self.assertEqual(payload["priceSeries"][-1]["splitClose"], 600.0)


if __name__ == "__main__":
    unittest.main()
