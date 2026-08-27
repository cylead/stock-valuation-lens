"""Normalize local SEC Company Facts and Stooq prices into SQLite."""

from __future__ import print_function

import csv
import datetime as dt
import json
import math
import os
import sqlite3
import tempfile

from .core import annual_duration, normalize_ticker, parse_date


SCHEMA_VERSION = 2
FORMS = {"10-K", "10-K/A"}
EXCHANGES = {"NASDAQ", "NYSE", "NYSE AMERICAN", "NYSE MKT"}

EPS_BASIC_TAGS = ("EarningsPerShareBasic",)
EPS_DILUTED_TAGS = ("EarningsPerShareDiluted",)
OCF_TAGS = (
    "NetCashProvidedByUsedInOperatingActivities",
    "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
)
CAPEX_TAGS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForAdditionsToPropertyPlantAndEquipment",
)
SHARES_TAGS = (
    "WeightedAverageNumberOfDilutedSharesOutstanding",
)
SPLIT_TAGS = (
    "StockholdersEquityNoteStockSplitConversionRatio1",
    "StockholdersEquityNoteStockSplitConversionRatio",
)


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE import_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE companies (
    ticker TEXT PRIMARY KEY,
    cik INTEGER NOT NULL,
    name TEXT NOT NULL,
    exchange TEXT,
    stooq_path TEXT NOT NULL,
    price_source TEXT NOT NULL DEFAULT 'Stooq',
    currency TEXT,
    reporting_source TEXT,
    is_sec_filer INTEGER NOT NULL DEFAULT 1,
    latest_adjusted_date TEXT,
    latest_split_date TEXT,
    has_basic_eps INTEGER NOT NULL DEFAULT 0,
    has_diluted_eps INTEGER NOT NULL DEFAULT 0,
    has_fcf INTEGER NOT NULL DEFAULT 0,
    has_fcf_per_share INTEGER NOT NULL DEFAULT 0,
    has_split_price INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX companies_name_idx ON companies(name COLLATE NOCASE);
CREATE INDEX companies_cik_idx ON companies(cik);

CREATE TABLE fundamentals (
    cik INTEGER NOT NULL,
    period_start TEXT,
    period_end TEXT NOT NULL,
    fiscal_year INTEGER NOT NULL,
    basic_eps REAL,
    diluted_eps REAL,
    ocf REAL,
    capex REAL,
    fcf REAL,
    diluted_shares REAL,
    fcf_per_share REAL,
    basic_source TEXT,
    diluted_source TEXT,
    ocf_source TEXT,
    capex_source TEXT,
    shares_source TEXT,
    fcf_source TEXT,
    PRIMARY KEY (cik, period_end)
);

CREATE INDEX fundamentals_cik_end_idx ON fundamentals(cik, period_end);

CREATE TABLE price_weekly (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    adjusted_close REAL NOT NULL,
    PRIMARY KEY (ticker, date),
    FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
);

CREATE INDEX price_weekly_ticker_date_idx ON price_weekly(ticker, date);

CREATE TABLE split_weekly (
    ticker TEXT NOT NULL,
    date TEXT NOT NULL,
    close REAL NOT NULL,
    PRIMARY KEY (ticker, date),
    FOREIGN KEY (ticker) REFERENCES companies(ticker) ON DELETE CASCADE
);

CREATE INDEX split_weekly_ticker_date_idx ON split_weekly(ticker, date);
"""


def _empty_audit():
    return {
        "generatedAt": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "summary": {
            "stooqStockFiles": 0,
            "matchedCompanies": 0,
            "unmatchedStooqTickers": 0,
            "missingCompanyFacts": 0,
            "companiesWithBasicEps": 0,
            "companiesWithDilutedEps": 0,
            "companiesWithFcf": 0,
            "companiesWithFcfPerShare": 0,
            "companiesWithSplitPrices": 0,
            "supplementalCompanies": 0,
            "duplicateFactsResolved": 0,
            "splitAdjustmentsApplied": 0,
            "malformedFiles": 0,
        },
        "unmatchedStooqTickers": [],
        "missingCompanyFacts": [],
        "unsupportedOrMissingFacts": [],
        "malformedFiles": [],
        "splitPriceWarnings": [],
        "notes": [
            "ETF directories are intentionally excluded from discovery.",
            "Raw source files are read-only and are never changed or deleted.",
        ],
    }


def _append_limited(audit, key, value, limit=1000):
    if len(audit[key]) < limit:
        audit[key].append(value)


def load_crosswalk(path):
    """Read either SEC company_tickers_exchange or company_tickers JSON."""
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    rows = []
    if isinstance(payload, dict) and "fields" in payload and "data" in payload:
        fields = payload["fields"]
        for values in payload["data"]:
            row = dict(zip(fields, values))
            rows.append(row)
    elif isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, dict) and "ticker" in value and "cik_str" in value:
                rows.append(
                    {
                        "ticker": value.get("ticker"),
                        "cik": value.get("cik_str"),
                        "name": value.get("title", ""),
                        "exchange": value.get("exchange"),
                    }
                )
    else:
        raise ValueError("Unsupported SEC crosswalk JSON structure")

    result = {}
    for row in rows:
        ticker = normalize_ticker(row.get("ticker"))
        if not ticker:
            continue
        exchange = (row.get("exchange") or "").strip()
        if exchange and exchange.upper() not in EXCHANGES:
            continue
        try:
            cik = int(row.get("cik") if row.get("cik") is not None else row.get("cik_str"))
        except (TypeError, ValueError):
            continue
        result[ticker] = {
            "ticker": ticker,
            "cik": cik,
            "name": row.get("name") or row.get("title") or ticker,
            "exchange": exchange,
        }
    if not result:
        raise ValueError("SEC crosswalk contained no usable exchange-listed tickers")
    return result


def discover_stooq_stocks(root):
    """Return ticker/path/exchange records from stock directories only."""
    records = {}
    for directory, subdirs, files in os.walk(root):
        lower_directory = directory.lower()
        subdirs[:] = [name for name in subdirs if "etf" not in name.lower()]
        if "etf" in lower_directory or "stocks" not in lower_directory:
            continue
        if "nasdaq" in lower_directory:
            exchange = "Nasdaq"
        elif "nysemkt" in lower_directory or "nyse mkt" in lower_directory:
            exchange = "NYSE American"
        elif "nyse" in lower_directory:
            exchange = "NYSE"
        else:
            exchange = ""
        for filename in files:
            if not filename.lower().endswith(".us.txt"):
                continue
            ticker = normalize_ticker(filename[:-7])
            records[ticker] = {
                "ticker": ticker,
                "path": os.path.join(directory, filename),
                "exchange": exchange,
            }
    return records


def load_supplemental_companies(path):
    """Load locally curated, non-SEC companies and their reported facts.

    The supplemental file is intentionally small and explicit: it lets the
    application include issuers that do not publish SEC Company Facts, while
    keeping their price and annual-report provenance alongside the dataset.
    """
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or not isinstance(payload.get("companies"), list):
        raise ValueError("Supplemental company JSON must contain a companies array")

    base = os.path.dirname(os.path.abspath(path))
    result = {}
    for item in payload["companies"]:
        if not isinstance(item, dict):
            raise ValueError("Supplemental company entry must be an object")
        ticker = normalize_ticker(item.get("ticker"))
        price_file = item.get("priceFile")
        if not ticker or not price_file:
            raise ValueError("Supplemental company requires ticker and priceFile")
        if ticker in result:
            raise ValueError("Duplicate supplemental ticker: %s" % ticker)
        try:
            entity_id = int(item["entityId"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("Supplemental company %s requires integer entityId" % ticker)
        if entity_id >= 0:
            raise ValueError("Supplemental entityId must be negative: %s" % ticker)
        facts = item.get("fundamentals", [])
        if not isinstance(facts, list):
            raise ValueError("Supplemental fundamentals must be an array: %s" % ticker)
        result[ticker] = {
            "ticker": ticker,
            "cik": entity_id,
            "name": item.get("name") or ticker,
            "exchange": item.get("exchange") or "",
            "path": os.path.abspath(os.path.join(base, price_file)),
            "price_source": item.get("priceSource") or "Supplemental price history",
            "currency": item.get("currency") or "",
            "reporting_source": item.get("reportingSource") or "Supplemental annual reports",
            "is_sec_filer": 0,
            "fundamentals": facts,
            "supplemental": True,
        }
    return result


def _fact_candidates(facts, tags, allowed_units, audit):
    candidates = {}
    for priority, tag in enumerate(tags):
        concept = facts.get(tag)
        if not concept:
            continue
        units = concept.get("units", {})
        for unit in allowed_units:
            for fact in units.get(unit, []):
                if fact.get("form") not in FORMS or fact.get("fp") != "FY":
                    continue
                start = fact.get("start")
                end = fact.get("end")
                duration = annual_duration(start, end)
                if duration is None or duration < 300 or duration > 430:
                    continue
                try:
                    value = float(fact["val"])
                except (KeyError, TypeError, ValueError):
                    continue
                key = (start, end)
                record = {
                    "tag": tag,
                    "unit": unit,
                    "start": start,
                    "end": end,
                    "value": value,
                    "filed": fact.get("filed") or "",
                    "accn": fact.get("accn") or "",
                    "form": fact.get("form"),
                    "priority": priority,
                }
                current = candidates.get(key)
                if current is None:
                    candidates[key] = record
                else:
                    audit["summary"]["duplicateFactsResolved"] += 1
                    current_rank = (current["priority"], -_date_rank(current["filed"]))
                    record_rank = (record["priority"], -_date_rank(record["filed"]))
                    if record_rank < current_rank:
                        candidates[key] = record
    return candidates


def _date_rank(value):
    try:
        return int(value.replace("-", ""))
    except (AttributeError, ValueError):
        return 0


def _source(record):
    if not record:
        return None
    payload = {
            "tag": record["tag"],
            "unit": record["unit"],
            "start": record["start"],
            "end": record["end"],
            "filed": record["filed"],
            "accession": record["accn"],
            "form": record["form"],
        }
    if record.get("split_adjustments"):
        payload["reportedValue"] = record.get("reported_value")
        payload["splitAdjustments"] = record["split_adjustments"]
    return json.dumps(
        payload,
        separators=(",", ":"),
        sort_keys=True,
    )


def _split_events(facts):
    """Extract unique standard-taxonomy stock split/reverse-split events."""
    events = {}
    for tag in SPLIT_TAGS:
        concept = facts.get(tag) or {}
        for fact in concept.get("units", {}).get("pure", []):
            end = fact.get("end")
            try:
                ratio = float(fact.get("val"))
                parse_date(end)
            except (TypeError, ValueError):
                continue
            if ratio <= 0:
                continue
            events[end] = {
                "date": end,
                "ratio": ratio,
                "tag": tag,
                "filed": fact.get("filed"),
                "accession": fact.get("accn"),
            }
    return [events[key] for key in sorted(events)]


def _apply_later_splits(record, split_events, mode, audit):
    """Restate a per-share/share-count fact for splits after it was filed.

    Comparative facts filed after a split are already retrospectively restated
    under GAAP, so only facts whose filing predates the split event are changed.
    """
    if not record:
        return None
    adjusted = dict(record)
    original = adjusted["value"]
    applications = []
    for event in split_events:
        if event["date"] <= adjusted["end"]:
            continue
        if adjusted.get("filed") and adjusted["filed"] >= event["date"]:
            continue
        ratio = event["ratio"]
        if mode == "per_share":
            adjusted["value"] /= ratio
        elif mode == "shares":
            adjusted["value"] *= ratio
        else:
            raise ValueError("Unknown split adjustment mode: %s" % mode)
        applications.append(event)
        audit["summary"]["splitAdjustmentsApplied"] += 1
    if applications:
        adjusted["reported_value"] = original
        adjusted["split_adjustments"] = applications
    return adjusted


def extract_fundamentals(payload, audit):
    """Extract reported annual EPS and conservative simple FCF values."""
    gaap = payload.get("facts", {}).get("us-gaap", {})
    if not gaap:
        return []
    basic = _fact_candidates(gaap, EPS_BASIC_TAGS, ("USD/shares",), audit)
    diluted = _fact_candidates(gaap, EPS_DILUTED_TAGS, ("USD/shares",), audit)
    ocf = _fact_candidates(gaap, OCF_TAGS, ("USD",), audit)
    capex = _fact_candidates(gaap, CAPEX_TAGS, ("USD",), audit)
    shares = _fact_candidates(gaap, SHARES_TAGS, ("shares",), audit)
    split_events = _split_events(gaap)

    keys = set(basic) | set(diluted) | set(ocf) | set(capex) | set(shares)
    by_end = {}
    for key in sorted(keys):
        start, end = key
        basic_record = _apply_later_splits(basic.get(key), split_events, "per_share", audit)
        diluted_record = _apply_later_splits(diluted.get(key), split_events, "per_share", audit)
        ocf_record = ocf.get(key)
        capex_record = capex.get(key)
        shares_record = _apply_later_splits(shares.get(key), split_events, "shares", audit)
        fcf = None
        fcf_per_share = None
        if ocf_record and capex_record:
            fcf = ocf_record["value"] - capex_record["value"]
            if shares_record and shares_record["value"] > 0:
                fcf_per_share = fcf / shares_record["value"]
        record = {
            "period_start": start,
            "period_end": end,
            "fiscal_year": parse_date(end).year,
            "basic_eps": basic_record["value"] if basic_record else None,
            "diluted_eps": diluted_record["value"] if diluted_record else None,
            "ocf": ocf_record["value"] if ocf_record else None,
            "capex": capex_record["value"] if capex_record else None,
            "fcf": fcf,
            "diluted_shares": shares_record["value"] if shares_record else None,
            "fcf_per_share": fcf_per_share,
            "basic_source": _source(basic_record),
            "diluted_source": _source(diluted_record),
            "ocf_source": _source(ocf_record),
            "capex_source": _source(capex_record),
            "shares_source": _source(shares_record),
            "fcf_source": json.dumps(
                {"ocf": json.loads(_source(ocf_record)), "capex": json.loads(_source(capex_record))},
                separators=(",", ":"),
                sort_keys=True,
            ) if ocf_record and capex_record else None,
        }
        previous = by_end.get(end)
        tracked_fields = (
            "basic_eps", "diluted_eps", "ocf", "capex", "fcf", "diluted_shares",
            "fcf_per_share", "basic_source", "diluted_source", "ocf_source",
            "capex_source", "shares_source", "fcf_source",
        )
        if previous is None:
            record["_field_starts"] = {
                field: start for field in tracked_fields if record.get(field) is not None
            }
            by_end[end] = record
        else:
            previous["period_start"] = min(previous["period_start"], start)
            for field in tracked_fields:
                if record.get(field) is None:
                    continue
                selected_start = previous["_field_starts"].get(field)
                if previous.get(field) is None or selected_start is None or start > selected_start:
                    previous[field] = record[field]
                    previous["_field_starts"][field] = start
    result = []
    for end in sorted(by_end):
        row = by_end[end]
        row.pop("_field_starts", None)
        result.append(row)
    return result


def read_stooq_weekly(path, max_price_years=25):
    """Parse a Stooq text file and retain the last trading row of each week."""
    daily = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header or "<DATE>" not in header or "<CLOSE>" not in header:
            raise ValueError("Unrecognized Stooq header")
        date_index = header.index("<DATE>")
        close_index = header.index("<CLOSE>")
        for row in reader:
            if len(row) <= max(date_index, close_index):
                continue
            try:
                date = dt.datetime.strptime(row[date_index], "%Y%m%d").date()
                close = float(row[close_index])
            except (TypeError, ValueError):
                continue
            if close <= 0:
                continue
            daily.append((date, close))
    return _weekly_rows(daily, max_price_years)


def _weekly_rows(daily, max_price_years):
    """Retain the last positive close of each ISO week from daily observations."""
    weekly = {}
    latest = None
    for date, close in daily:
        year, week, _ = date.isocalendar()
        weekly[(year, week)] = (date, close)
        if latest is None or date > latest:
            latest = date
    if latest and max_price_years:
        cutoff = latest - dt.timedelta(days=int(max_price_years * 365.2425))
    else:
        cutoff = None
    rows = [
        (date.isoformat(), close)
        for date, close in sorted(weekly.values())
        if cutoff is None or date >= cutoff
    ]
    return rows, latest.isoformat() if latest else None


def read_supplemental_weekly(path, max_price_years=25):
    """Read a supplemental CSV containing unadjusted ``date,close`` prices."""
    daily = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        field_map = {
            (name or "").strip().lower(): name
            for name in (reader.fieldnames or [])
        }
        if not {"date", "close"}.issubset(field_map):
            raise ValueError("Supplemental price CSV must contain date,close columns")
        for row in reader:
            try:
                date = parse_date((row.get(field_map["date"]) or "").strip())
                close = float(row.get(field_map["close"]))
            except (TypeError, ValueError):
                continue
            if math.isfinite(close) and close > 0:
                daily.append((date, close))
    return _weekly_rows(daily, max_price_years)


def _supplemental_source(fact, metric):
    source = fact.get(metric + "Source") or fact.get("source")
    if not source:
        return None
    if not isinstance(source, dict):
        raise ValueError("Supplemental fact source must be an object")
    payload = dict(source)
    payload.setdefault("metric", metric)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _supplemental_number(fact, key):
    value = fact.get(key)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError("Invalid supplemental %s" % key)
    if not math.isfinite(number):
        raise ValueError("Invalid supplemental %s" % key)
    return number


def extract_supplemental_fundamentals(facts):
    """Normalize curated annual-report data into the standard facts schema."""
    rows = []
    seen = set()
    for fact in facts:
        if not isinstance(fact, dict):
            raise ValueError("Supplemental fundamental must be an object")
        end = parse_date(fact.get("periodEnd")).isoformat()
        start = parse_date(fact.get("periodStart") or (end[:4] + "-01-01")).isoformat()
        if start > end:
            raise ValueError("Supplemental period start is after period end")
        if end in seen:
            raise ValueError("Duplicate supplemental fiscal period: %s" % end)
        seen.add(end)
        ocf = _supplemental_number(fact, "ocf")
        capex = _supplemental_number(fact, "capex")
        fcf = _supplemental_number(fact, "fcf")
        shares = _supplemental_number(fact, "dilutedShares")
        fcf_per_share = _supplemental_number(fact, "fcfPerShare")
        if fcf is None and ocf is not None and capex is not None:
            fcf = ocf - capex
        if fcf_per_share is None and fcf is not None and shares is not None and shares > 0:
            fcf_per_share = fcf / shares
        rows.append(
            {
                "period_start": start,
                "period_end": end,
                "fiscal_year": int(fact.get("fiscalYear") or end[:4]),
                "basic_eps": _supplemental_number(fact, "basicEps"),
                "diluted_eps": _supplemental_number(fact, "dilutedEps"),
                "ocf": ocf,
                "capex": capex,
                "fcf": fcf,
                "diluted_shares": shares,
                "fcf_per_share": fcf_per_share,
                "basic_source": _supplemental_source(fact, "basicEps"),
                "diluted_source": _supplemental_source(fact, "dilutedEps"),
                "ocf_source": _supplemental_source(fact, "ocf"),
                "capex_source": _supplemental_source(fact, "capex"),
                "shares_source": _supplemental_source(fact, "dilutedShares"),
                "fcf_source": _supplemental_source(fact, "fcf"),
            }
        )
    return sorted(rows, key=lambda item: item["period_end"])


def _prepare_database(path):
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    return connection


def _insert_split_prices(connection, path, known_tickers, audit, batch_size=10000):
    connection.execute(
        "CREATE TEMP TABLE split_stage (ticker TEXT, date TEXT, close REAL, "
        "PRIMARY KEY(ticker, date)) WITHOUT ROWID"
    )
    batch = []
    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"ticker", "date", "close"}
        if not reader.fieldnames or not required.issubset({name.strip().lower() for name in reader.fieldnames}):
            raise ValueError("Split price CSV must contain ticker,date,close columns")
        field_map = {name.strip().lower(): name for name in reader.fieldnames}
        for line_number, row in enumerate(reader, 2):
            ticker = normalize_ticker(row.get(field_map["ticker"]))
            if ticker not in known_tickers:
                continue
            try:
                date = parse_date(row.get(field_map["date"])).isoformat()
                close = float(row.get(field_map["close"]))
            except (TypeError, ValueError):
                _append_limited(
                    audit,
                    "splitPriceWarnings",
                    {"line": line_number, "reason": "invalid date or close"},
                )
                continue
            if close <= 0:
                _append_limited(
                    audit,
                    "splitPriceWarnings",
                    {"line": line_number, "ticker": ticker, "reason": "non-positive close"},
                )
                continue
            batch.append((ticker, date, close))
            if len(batch) >= batch_size:
                connection.executemany(
                    "INSERT OR REPLACE INTO split_stage(ticker,date,close) VALUES (?,?,?)",
                    batch,
                )
                batch = []
        if batch:
            connection.executemany(
                "INSERT OR REPLACE INTO split_stage(ticker,date,close) VALUES (?,?,?)",
                batch,
            )

    connection.execute(
        "INSERT INTO split_weekly(ticker,date,close) "
        "SELECT ticker,date,close FROM ("
        " SELECT ticker,date,close,ROW_NUMBER() OVER ("
        "  PARTITION BY ticker,strftime('%Y-%W',date) ORDER BY date DESC"
        " ) AS rank FROM split_stage"
        ") WHERE rank=1"
    )
    connection.execute(
        "UPDATE companies SET latest_split_date=("
        " SELECT MAX(date) FROM split_weekly WHERE split_weekly.ticker=companies.ticker"
        "), has_split_price=EXISTS("
        " SELECT 1 FROM split_weekly WHERE split_weekly.ticker=companies.ticker"
        ")"
    )
    connection.execute("DROP TABLE split_stage")

    mismatch_rows = connection.execute(
        "SELECT s.ticker,s.date,s.close,a.adjusted_close FROM split_weekly s "
        "JOIN price_weekly a ON a.ticker=s.ticker AND a.date=s.date "
        "WHERE s.date=(SELECT MAX(s2.date) FROM split_weekly s2 "
        " WHERE s2.ticker=s.ticker AND EXISTS(SELECT 1 FROM price_weekly a2 "
        " WHERE a2.ticker=s2.ticker AND a2.date=s2.date))"
    ).fetchall()
    for ticker, date, split_close, adjusted_close in mismatch_rows:
        ratio = split_close / adjusted_close if adjusted_close else None
        if ratio is not None and (ratio < 0.75 or ratio > 1.25):
            _append_limited(
                audit,
                "splitPriceWarnings",
                {
                    "ticker": ticker,
                    "date": date,
                    "reason": "latest overlapping close differs from Stooq by more than 25%",
                    "ratio": ratio,
                },
            )


def build_database(
    sec_dir,
    stooq_dir,
    crosswalk_path,
    output_path,
    audit_path,
    split_prices_path=None,
    supplemental_path=None,
    tickers=None,
    max_price_years=25,
    force=False,
):
    """Build a complete database atomically and return the audit dictionary."""
    output_path = os.path.abspath(output_path)
    audit_path = os.path.abspath(audit_path)
    if os.path.exists(output_path) and not force:
        raise FileExistsError("Database exists; pass --force to replace it: %s" % output_path)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    os.makedirs(os.path.dirname(audit_path) or ".", exist_ok=True)

    audit = _empty_audit()
    crosswalk = load_crosswalk(crosswalk_path)
    stooq = discover_stooq_stocks(stooq_dir)
    supplemental = load_supplemental_companies(supplemental_path)
    audit["summary"]["stooqStockFiles"] = len(stooq)
    requested = {normalize_ticker(item) for item in tickers} if tickers else None
    matched = {}
    for ticker, price_record in stooq.items():
        if requested is not None and ticker not in requested:
            continue
        company = crosswalk.get(ticker)
        if not company:
            audit["summary"]["unmatchedStooqTickers"] += 1
            _append_limited(audit, "unmatchedStooqTickers", ticker)
            continue
        record = dict(company)
        record.update(price_record)
        matched[ticker] = record
    for ticker, record in supplemental.items():
        if requested is not None and ticker not in requested:
            continue
        if ticker in matched:
            raise ValueError("Supplemental ticker duplicates a SEC/Stooq company: %s" % ticker)
        matched[ticker] = record
        audit["summary"]["supplementalCompanies"] += 1
    audit["summary"]["matchedCompanies"] = len(matched)

    descriptor, temp_path = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(output_path), suffix=".tmp", dir=os.path.dirname(output_path) or "."
    )
    os.close(descriptor)
    try:
        os.unlink(temp_path)
        connection = _prepare_database(temp_path)
        try:
            connection.execute(
                "INSERT INTO import_meta(key,value) VALUES (?,?)",
                ("built_at", audit["generatedAt"]),
            )
            connection.execute(
                "INSERT INTO import_meta(key,value) VALUES (?,?)",
                ("max_price_years", str(max_price_years)),
            )
            for ticker in sorted(matched):
                record = matched[ticker]
                connection.execute(
                    "INSERT INTO companies("
                    "ticker,cik,name,exchange,stooq_path,price_source,currency,reporting_source,is_sec_filer"
                    ") VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        ticker, record["cik"], record["name"], record["exchange"], record["path"],
                        record.get("price_source", "Stooq"), record.get("currency", "USD"),
                        record.get("reporting_source", "SEC Company Facts"),
                        record.get("is_sec_filer", 1),
                    ),
                )

            processed_ciks = set()
            for ticker in sorted(matched):
                record = matched[ticker]
                cik = record["cik"]
                if record.get("supplemental"):
                    try:
                        rows = extract_supplemental_fundamentals(record["fundamentals"])
                        for row in rows:
                            connection.execute(
                                "INSERT OR REPLACE INTO fundamentals("
                                "cik,period_start,period_end,fiscal_year,basic_eps,diluted_eps,ocf,capex,fcf,"
                                "diluted_shares,fcf_per_share,basic_source,diluted_source,ocf_source,capex_source,"
                                "shares_source,fcf_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                (
                                    cik, row["period_start"], row["period_end"], row["fiscal_year"],
                                    row["basic_eps"], row["diluted_eps"], row["ocf"], row["capex"],
                                    row["fcf"], row["diluted_shares"], row["fcf_per_share"],
                                    row["basic_source"], row["diluted_source"], row["ocf_source"],
                                    row["capex_source"], row["shares_source"], row["fcf_source"],
                                ),
                            )
                        weekly, latest = read_supplemental_weekly(
                            record["path"], max_price_years=max_price_years
                        )
                        connection.executemany(
                            "INSERT INTO split_weekly(ticker,date,close) VALUES (?,?,?)",
                            [(ticker, date, close) for date, close in weekly],
                        )
                        connection.execute(
                            "UPDATE companies SET latest_split_date=?,has_split_price=? WHERE ticker=?",
                            (latest, int(bool(weekly)), ticker),
                        )
                    except (OSError, ValueError, json.JSONDecodeError) as exc:
                        audit["summary"]["malformedFiles"] += 1
                        _append_limited(
                            audit,
                            "malformedFiles",
                            {"path": record["path"], "error": str(exc)},
                        )
                    continue
                facts_path = os.path.join(sec_dir, "CIK%010d.json" % cik)
                if cik not in processed_ciks:
                    processed_ciks.add(cik)
                    if not os.path.exists(facts_path):
                        audit["summary"]["missingCompanyFacts"] += 1
                        _append_limited(
                            audit,
                            "missingCompanyFacts",
                            {"ticker": ticker, "cik": cik, "path": facts_path},
                        )
                    else:
                        try:
                            with open(facts_path, "r", encoding="utf-8") as handle:
                                payload = json.load(handle)
                            rows = extract_fundamentals(payload, audit)
                            for row in rows:
                                connection.execute(
                                    "INSERT OR REPLACE INTO fundamentals("
                                    "cik,period_start,period_end,fiscal_year,basic_eps,diluted_eps,ocf,capex,fcf,"
                                    "diluted_shares,fcf_per_share,basic_source,diluted_source,ocf_source,capex_source,"
                                    "shares_source,fcf_source) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                    (
                                        cik, row["period_start"], row["period_end"], row["fiscal_year"],
                                        row["basic_eps"], row["diluted_eps"], row["ocf"], row["capex"],
                                        row["fcf"], row["diluted_shares"], row["fcf_per_share"],
                                        row["basic_source"], row["diluted_source"], row["ocf_source"],
                                        row["capex_source"], row["shares_source"], row["fcf_source"],
                                    ),
                                )
                        except (OSError, ValueError, json.JSONDecodeError) as exc:
                            audit["summary"]["malformedFiles"] += 1
                            _append_limited(
                                audit,
                                "malformedFiles",
                                {"path": facts_path, "error": str(exc)},
                            )

                try:
                    weekly, latest = read_stooq_weekly(record["path"], max_price_years=max_price_years)
                    connection.executemany(
                        "INSERT INTO price_weekly(ticker,date,adjusted_close) VALUES (?,?,?)",
                        [(ticker, date, close) for date, close in weekly],
                    )
                    connection.execute(
                        "UPDATE companies SET latest_adjusted_date=? WHERE ticker=?",
                        (latest, ticker),
                    )
                except (OSError, ValueError) as exc:
                    audit["summary"]["malformedFiles"] += 1
                    _append_limited(
                        audit,
                        "malformedFiles",
                        {"path": record["path"], "error": str(exc)},
                    )

            connection.execute(
                "UPDATE companies SET "
                "has_basic_eps=EXISTS(SELECT 1 FROM fundamentals f WHERE f.cik=companies.cik AND f.basic_eps IS NOT NULL),"
                "has_diluted_eps=EXISTS(SELECT 1 FROM fundamentals f WHERE f.cik=companies.cik AND f.diluted_eps IS NOT NULL),"
                "has_fcf=EXISTS(SELECT 1 FROM fundamentals f WHERE f.cik=companies.cik AND f.fcf IS NOT NULL),"
                "has_fcf_per_share=EXISTS(SELECT 1 FROM fundamentals f WHERE f.cik=companies.cik AND f.fcf_per_share IS NOT NULL)"
            )
            if split_prices_path:
                _insert_split_prices(connection, split_prices_path, set(matched), audit)

            counts = connection.execute(
                "SELECT COALESCE(SUM(has_basic_eps),0),COALESCE(SUM(has_diluted_eps),0),"
                "COALESCE(SUM(has_fcf),0),COALESCE(SUM(has_fcf_per_share),0),"
                "COALESCE(SUM(has_split_price),0) FROM companies"
            ).fetchone()
            keys = (
                "companiesWithBasicEps", "companiesWithDilutedEps", "companiesWithFcf",
                "companiesWithFcfPerShare", "companiesWithSplitPrices",
            )
            for key, count in zip(keys, counts):
                audit["summary"][key] = int(count)

            missing_rows = connection.execute(
                "SELECT ticker,has_basic_eps,has_diluted_eps,has_fcf,has_fcf_per_share "
                "FROM companies WHERE NOT(has_basic_eps AND has_diluted_eps AND has_fcf_per_share)"
            ).fetchall()
            for ticker, basic_ok, diluted_ok, fcf_ok, fcf_share_ok in missing_rows:
                missing = []
                if not basic_ok:
                    missing.append("basic EPS")
                if not diluted_ok:
                    missing.append("diluted EPS")
                if not fcf_ok:
                    missing.append("FCF (OCF and cash capex)")
                elif not fcf_share_ok:
                    missing.append("diluted shares for FCF/share")
                _append_limited(
                    audit,
                    "unsupportedOrMissingFacts",
                    {"ticker": ticker, "missing": missing},
                )
            connection.execute(
                "INSERT OR REPLACE INTO import_meta(key,value) VALUES (?,?)",
                ("audit_summary", json.dumps(audit["summary"], sort_keys=True)),
            )
            connection.commit()
            connection.execute("PRAGMA optimize")
        finally:
            connection.close()

        with open(audit_path, "w", encoding="utf-8") as handle:
            json.dump(audit, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temp_path, output_path)
    except Exception:
        if os.path.exists(temp_path):
            os.unlink(temp_path)
        raise
    return audit
