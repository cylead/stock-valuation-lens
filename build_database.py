#!/usr/bin/env python3
"""Command-line entry point for building the normalized database."""

from __future__ import print_function

import argparse
import json

from fastfunds.importer import build_database


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--sec-dir", default="companyfacts", help="SEC Company Facts directory")
    value.add_argument("--stooq-dir", default="daily", help="Stooq daily-data root")
    value.add_argument("--sec-tickers", required=True, help="SEC company_tickers_exchange.json path")
    value.add_argument("--split-prices", help="Optional ticker,date,close split-only CSV")
    value.add_argument(
        "--supplemental-data",
        default="supplemental_data/companies.json",
        help="Optional curated non-SEC company JSON (default: supplemental_data/companies.json)",
    )
    value.add_argument("--db", default="app_data/fastfunds.sqlite3", help="Output SQLite database")
    value.add_argument("--audit", default="app_data/import_audit.json", help="Output audit JSON")
    value.add_argument("--tickers", help="Optional comma-separated validation/watchlist subset")
    value.add_argument("--max-price-years", type=int, default=25, help="Weekly price history to retain; 0 keeps all")
    value.add_argument("--force", action="store_true", help="Replace an existing derived database")
    return value


def main():
    args = parser().parse_args()
    tickers = [item.strip() for item in args.tickers.split(",")] if args.tickers else None
    audit = build_database(
        sec_dir=args.sec_dir,
        stooq_dir=args.stooq_dir,
        crosswalk_path=args.sec_tickers,
        split_prices_path=args.split_prices,
        output_path=args.db,
        audit_path=args.audit,
        tickers=tickers,
        max_price_years=args.max_price_years,
        force=args.force,
        supplemental_path=args.supplemental_data,
    )
    print(json.dumps(audit["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
