"""Download original public NSE XBRL from a timestamped index export.

Input is the audited index JSON with symbol, source, period_end, published_times
and index_source. This never infers publication dates from fiscal periods.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
from pathlib import Path


def main():
    from curl_cffi import requests
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "xml").mkdir(exist_ok=True)
    if (args.output / "downloads.json").exists():
        raise RuntimeError("Download ledger already frozen; use another output directory")
    records = json.loads(args.index.read_text())["filings"]
    def fetch(record):
        source = record["source"]
        if not source.startswith("https://nsearchives.nseindia.com/corporate/") or not source.endswith(".xml"):
            return {**record, "status": "error", "reason": "Only original NSE corporate XML links are accepted"}
        try:
            response = requests.get(source, impersonate="chrome", timeout=12)
            response.raise_for_status()
            if "xbrl" not in response.text[:3000].lower():
                raise ValueError("Response is not the original XBRL document")
            filename = source.rsplit("/", 1)[-1]
            (args.output / "xml" / filename).write_bytes(response.content)
            return {**record, "status": "downloaded", "file": filename, "retrieval": "Direct public NSE archive HTTP"}
        except Exception as error:
            return {**record, "status": "error", "reason": str(error)[:250]}
    result = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        for future in as_completed([executor.submit(fetch, r) for r in records]):
            result.append(future.result())
    (args.output / "downloads.json").write_text(json.dumps({"receipts": result, "total": len(result), "failed": sum(r["status"] != "downloaded" for r in result)}, indent=2))
    print(f"Downloaded {sum(r['status'] == 'downloaded' for r in result)}/{len(result)} original filings")


if __name__ == "__main__":
    main()
