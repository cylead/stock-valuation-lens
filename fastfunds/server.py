"""Read-only local HTTP server and JSON API."""

from __future__ import print_function

import argparse
import datetime as dt
import json
import mimetypes
import os
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .core import METRICS, normalize_ticker, parse_date, valuation_summary


STATIC_ROOT = os.path.join(os.path.dirname(__file__), "static")


def _connection(path):
    uri = "file:%s?mode=ro" % os.path.abspath(path)
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def _json_source(value):
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return {"raw": value}


def company_payload(connection, ticker):
    row = connection.execute(
        "SELECT ticker,cik,name,exchange,price_source,currency,reporting_source,is_sec_filer,"
        "latest_adjusted_date,latest_split_date,has_basic_eps,has_diluted_eps,"
        "has_dividend_per_share,has_fcf,has_fcf_per_share,has_split_price "
        "FROM companies WHERE ticker=?",
        (normalize_ticker(ticker),),
    ).fetchone()
    if row is None:
        return None
    availability = {
        "eps_basic": bool(row["has_basic_eps"]),
        "eps_diluted": bool(row["has_diluted_eps"]),
        "dividend_per_share": bool(row["has_dividend_per_share"]),
        "fcf_total": bool(row["has_fcf"]),
        "fcf_per_share": bool(row["has_fcf_per_share"]),
        "split_price": bool(row["has_split_price"]),
    }
    reasons = []
    fact_description = "reported annual GAAP" if row["is_sec_filer"] else "reported annual"
    if not availability["eps_basic"]:
        reasons.append("No %s basic EPS is available." % fact_description)
    if not availability["eps_diluted"]:
        reasons.append("No %s diluted EPS is available." % fact_description)
    if not availability["dividend_per_share"]:
        reasons.append("No %s common-stock dividend per share is available." % fact_description)
    if not availability["fcf_total"]:
        reasons.append("FCF unavailable because matching annual OCF and cash-capex facts were not found.")
    elif not availability["fcf_per_share"]:
        reasons.append("FCF/share unavailable because annual diluted weighted-average shares were not found.")
    return {
        "ticker": row["ticker"],
        "cik": row["cik"],
        "name": row["name"],
        "exchange": row["exchange"],
        "priceSource": row["price_source"],
        "currency": row["currency"],
        "reportingSource": row["reporting_source"],
        "isSecFiler": bool(row["is_sec_filer"]),
        "latestAdjustedDate": row["latest_adjusted_date"],
        "latestSplitDate": row["latest_split_date"],
        "availability": availability,
        "availabilityNotes": reasons,
    }


def search_companies(connection, query, limit=20):
    query = (query or "").strip()[:64]
    limit = max(1, min(int(limit), 50))
    if not query:
        rows = connection.execute(
            "SELECT ticker,name,exchange,has_basic_eps,has_diluted_eps,has_dividend_per_share,"
            "has_fcf_per_share,has_split_price "
            "FROM companies ORDER BY ticker LIMIT ?",
            (limit,),
        ).fetchall()
    else:
        upper = normalize_ticker(query)
        contains = "%" + query.replace("%", "\\%").replace("_", "\\_") + "%"
        prefix = upper.replace("%", "\\%").replace("_", "\\_") + "%"
        rows = connection.execute(
            "SELECT ticker,name,exchange,has_basic_eps,has_diluted_eps,has_dividend_per_share,"
            "has_fcf_per_share,has_split_price "
            "FROM companies WHERE ticker LIKE ? ESCAPE '\\' OR name LIKE ? ESCAPE '\\' COLLATE NOCASE "
            "ORDER BY CASE WHEN ticker=? THEN 0 WHEN ticker LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END, ticker LIMIT ?",
            (prefix, contains, upper, prefix, limit),
        ).fetchall()
    return [
        {
            "ticker": row["ticker"],
            "name": row["name"],
            "exchange": row["exchange"],
            "availability": {
                "eps_basic": bool(row["has_basic_eps"]),
                "eps_diluted": bool(row["has_diluted_eps"]),
                "dividend_per_share": bool(row["has_dividend_per_share"]),
                "fcf_per_share": bool(row["has_fcf_per_share"]),
                "split_price": bool(row["has_split_price"]),
            },
        }
        for row in rows
    ]


def _date_bounds(connection, ticker):
    row = connection.execute(
        "SELECT MIN(date) AS minimum,MAX(date) AS maximum FROM ("
        " SELECT date FROM price_weekly WHERE ticker=? UNION ALL "
        " SELECT date FROM split_weekly WHERE ticker=?"
        ")",
        (ticker, ticker),
    ).fetchone()
    return (row["minimum"], row["maximum"]) if row else (None, None)


def _normalize_range(minimum, maximum, start=None, end=None):
    if minimum is None or maximum is None:
        return start, end
    start_value = start or minimum
    end_value = end or maximum
    if len(start_value) == 4:
        start_value += "-01-01"
    if len(end_value) == 4:
        end_value += "-12-31"
    start_date = max(parse_date(minimum), parse_date(start_value))
    end_date = min(parse_date(maximum), parse_date(end_value))
    if start_date > end_date:
        raise ValueError("start must not be after end")
    return start_date.isoformat(), end_date.isoformat()


def _dividend_yield_series(dividend_history, price_series, use_split_prices):
    """Calculate weekly trailing reported dividend yield percentages."""
    result = []
    dividend_index = 0
    current = None
    price_key = "splitClose" if use_split_prices else "adjustedClose"
    price_type = "split_only_close" if use_split_prices else "stooq_adjusted_close"
    for price_row in price_series:
        while (
            dividend_index < len(dividend_history)
            and dividend_history[dividend_index]["date"] <= price_row["date"]
        ):
            current = dividend_history[dividend_index]
            dividend_index += 1
        price = price_row.get(price_key)
        if current is None or price is None or price <= 0 or current["value"] < 0:
            continue
        result.append(
            {
                "date": price_row["date"],
                "value": current["value"] / price * 100.0,
                "dividendPerShare": current["value"],
                "dividendDate": current["date"],
                "price": price,
                "priceType": price_type,
                "source": current["source"],
            }
        )
    return result


def build_chart_payload(connection, ticker, metric="eps_diluted", start=None, end=None, custom_multiple=None):
    ticker = normalize_ticker(ticker)
    if metric not in METRICS:
        raise ValueError("Unknown metric: %s" % metric)
    company = company_payload(connection, ticker)
    if company is None:
        return None
    minimum, maximum = _date_bounds(connection, ticker)
    start, end = _normalize_range(minimum, maximum, start, end)
    metric_info = METRICS[metric]

    where = "WHERE f.cik=?"
    params = [company["cik"]]
    if start:
        where += " AND f.period_end>=?"
        params.append(start)
    if end:
        where += " AND f.period_end<=?"
        params.append(end)
    rows = connection.execute(
        "SELECT f.period_start,f.period_end,f.fiscal_year,f.%s AS metric_value,"
        "f.dividend_per_share,f.dividend_source,f.fcf,f.fcf_per_share,f.ocf,f.capex,"
        "f.diluted_shares,f.%s AS source "
        "FROM fundamentals f %s ORDER BY f.period_end" % (
            metric_info["column"], metric_info["source_column"], where
        ),
        tuple(params),
    ).fetchall()
    fundamentals = [
        {
            "period_start": row["period_start"],
            "period_end": row["period_end"],
            "fiscalYear": row["fiscal_year"],
            "value": row["metric_value"],
            "dividendPerShare": row["dividend_per_share"],
            "fcf": row["fcf"],
            "fcfPerShare": row["fcf_per_share"],
            "ocf": row["ocf"],
            "capex": row["capex"],
            "dilutedShares": row["diluted_shares"],
            "source": _json_source(row["source"]),
        }
        for row in rows
    ]
    dividend_series = [
        {
            "date": row["period_end"],
            "fiscalYear": row["fiscal_year"],
            "value": row["dividend_per_share"],
            "source": _json_source(row["dividend_source"]),
        }
        for row in rows if row["dividend_per_share"] is not None
    ]
    dividend_params = [company["cik"]]
    dividend_where = "WHERE cik=? AND dividend_per_share IS NOT NULL"
    if end:
        dividend_where += " AND period_end<=?"
        dividend_params.append(end)
    dividend_rows = connection.execute(
        "SELECT period_end,dividend_per_share,dividend_source FROM fundamentals "
        + dividend_where + " ORDER BY period_end",
        tuple(dividend_params),
    ).fetchall()
    dividend_history = [
        {
            "date": row["period_end"],
            "value": row["dividend_per_share"],
            "source": _json_source(row["dividend_source"]),
        }
        for row in dividend_rows
    ]

    date_where = ""
    date_params = [ticker, ticker]
    if start and end:
        date_where = " WHERE dates.date BETWEEN ? AND ?"
        date_params.extend([start, end])
    price_rows = connection.execute(
        "WITH dates AS (SELECT date FROM price_weekly WHERE ticker=? UNION SELECT date FROM split_weekly WHERE ticker=?) "
        "SELECT dates.date,s.close,a.adjusted_close FROM dates "
        "LEFT JOIN split_weekly s ON s.ticker=? AND s.date=dates.date "
        "LEFT JOIN price_weekly a ON a.ticker=? AND a.date=dates.date" + date_where + " ORDER BY dates.date",
        tuple(date_params[:2] + [ticker, ticker] + date_params[2:]),
    ).fetchall()
    price_series = [
        {"date": row["date"], "splitClose": row["close"], "adjustedClose": row["adjusted_close"]}
        for row in price_rows
    ]
    dividend_yield_series = _dividend_yield_series(
        dividend_history,
        price_series,
        company["availability"]["split_price"],
    )
    valuation = valuation_summary(fundamentals, custom_multiple=custom_multiple)
    warnings = list(company["availabilityNotes"])
    if not fundamentals:
        warnings.append("No annual observations for this metric in the selected window.")
    if valuation["cagr"] is None:
        warnings.append("CAGR and formula valuation require at least two positive annual observations.")
    return {
        "company": company,
        "metric": {"id": metric, "label": metric_info["label"]},
        "bounds": {"minimum": minimum, "maximum": maximum, "start": start, "end": end},
        "fundamentals": fundamentals,
        "dividendSeries": dividend_series,
        "dividendYieldSeries": dividend_yield_series,
        "priceSeries": price_series,
        "valuation": valuation,
        "warnings": list(dict.fromkeys(warnings)),
        "generatedAt": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


class RequestHandler(BaseHTTPRequestHandler):
    server_version = "FastFunds/1.0"

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))

    def _send_json(self, payload, status=200):
        encoded = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _send_static(self, path):
        name = "index.html" if path in ("", "/") else unquote(path).lstrip("/")
        allowed = {"index.html", "app.js", "styles.css"}
        if name not in allowed:
            self.send_error(404)
            return
        file_path = os.path.join(STATIC_ROOT, name)
        try:
            with open(file_path, "rb") as handle:
                content = handle.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file_path)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self._send_static(parsed.path)
            return
        try:
            with _connection(self.server.database_path) as connection:
                params = parse_qs(parsed.query)
                if parsed.path == "/api/health":
                    count = connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
                    self._send_json({"ok": True, "companies": count})
                    return
                if parsed.path == "/api/companies":
                    query = params.get("q", [""])[0]
                    limit = params.get("limit", ["20"])[0]
                    self._send_json({"companies": search_companies(connection, query, limit)})
                    return
                if parsed.path.startswith("/api/company/"):
                    ticker = parsed.path[len("/api/company/"):]
                    payload = company_payload(connection, ticker)
                    if payload is None:
                        self._send_json({"error": "Company not found"}, 404)
                    else:
                        self._send_json(payload)
                    return
                if parsed.path.startswith("/api/chart/"):
                    ticker = parsed.path[len("/api/chart/"):]
                    payload = build_chart_payload(
                        connection,
                        ticker,
                        metric=params.get("metric", ["eps_diluted"])[0],
                        start=params.get("start", [None])[0],
                        end=params.get("end", [None])[0],
                        custom_multiple=params.get("multiple", [None])[0],
                    )
                    if payload is None:
                        self._send_json({"error": "Company not found"}, 404)
                    else:
                        self._send_json(payload)
                    return
                self._send_json({"error": "API route not found"}, 404)
        except (ValueError, sqlite3.Error) as exc:
            self._send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self._send_json({"error": "Internal error", "detail": str(exc)}, 500)


def run(database_path, host="127.0.0.1", port=8765):
    if not os.path.exists(database_path):
        raise FileNotFoundError("Database not found: %s" % database_path)
    server = ThreadingHTTPServer((host, port), RequestHandler)
    server.database_path = os.path.abspath(database_path)
    print("Stock Valuation Lens running at http://%s:%d" % (host, port))
    print("Using database: %s" % server.database_path)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="app_data/fastfunds.sqlite3")
    parser.add_argument("--host", default="127.0.0.1", choices=("127.0.0.1", "localhost"))
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    run(args.db, args.host, args.port)


if __name__ == "__main__":
    main()
