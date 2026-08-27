#!/usr/bin/env python3
"""Convert a saved Yahoo Finance chart response to local date,close CSV.

The database build never contacts the network. This small refresh helper turns
a manually downloaded MC.PA chart response into the checked-in supplemental
price input, retaining Yahoo's historical ``close`` rather than ``adjclose``.
"""

from __future__ import print_function

import argparse
import csv
import datetime as dt
import json
import math
import os
import tempfile


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Saved Yahoo Finance v8 chart JSON")
    parser.add_argument(
        "--output",
        default="supplemental_data/mc.pa.csv",
        help="Output CSV path (default: supplemental_data/mc.pa.csv)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    with open(args.input, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    result = payload["chart"]["result"][0]
    timestamps = result["timestamp"]
    closes = result["indicators"]["quote"][0]["close"]
    if len(timestamps) != len(closes):
        raise ValueError("Mismatched Yahoo timestamp and close arrays")

    rows = []
    for timestamp, close in zip(timestamps, closes):
        if close is None:
            continue
        close = float(close)
        if not math.isfinite(close) or close <= 0:
            continue
        date = dt.datetime.fromtimestamp(int(timestamp), tz=dt.timezone.utc).date().isoformat()
        rows.append((date, "%.6f" % close))
    if not rows:
        raise ValueError("No usable closes in Yahoo chart response")

    output = os.path.abspath(args.output)
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".%s." % os.path.basename(output), suffix=".tmp", dir=os.path.dirname(output) or "."
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["date", "close"])
            writer.writerows(rows)
        os.replace(temporary, output)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    print("Wrote %d closes through %s to %s" % (len(rows), rows[-1][0], output))


if __name__ == "__main__":
    main()
