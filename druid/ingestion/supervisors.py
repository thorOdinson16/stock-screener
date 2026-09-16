"""
supervisors.py — on-demand control of the Druid Kafka supervisors.

The platform is on-demand, so supervisors are kept **suspended** at rest (no
ingestion tasks running). A pipeline run resumes them, waits until they have
drained the newly published serving messages, then suspends them again.

Subcommands:
    suspend   suspend every registered supervisor (idempotent)
    resume    resume every registered supervisor
    wait      block until every supervisor's aggregate lag is 0
    status    print each supervisor's state and lag

The supervisor ids are read from the sibling `*-kafka.json` specs, so this stays
in sync with `submit.sh`.
"""

import argparse
import glob
import json
import os
import sys
import time
import urllib.error
import urllib.request

_HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROUTER = os.environ.get("DRUID_ROUTER", "http://localhost:8888")


def load_ids(directory: str = _HERE):
    ids = []
    for path in sorted(glob.glob(os.path.join(directory, "*-kafka.json"))):
        with open(path) as fh:
            spec = json.load(fh)
        data_source = (spec.get("dataSchema") or {}).get("dataSource")
        if data_source:
            ids.append(data_source)
    return ids


def drained(status_payloads) -> bool:
    """True when every supervisor reports zero aggregate lag. Supervisors that
    are not registered (payload is None) are ignored."""
    for payload in status_payloads:
        if payload is None:
            continue
        if payload.get("aggregateLag", 0) != 0:
            return False
    return True


def _request(method: str, path: str, router: str):
    request = urllib.request.Request(f"{router.rstrip('/')}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = response.read().decode()
            return response.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]
    except urllib.error.URLError as e:
        return None, str(e)


def supervisor_status(supervisor_id: str, router: str):
    status, body = _request(
        "GET", f"/druid/indexer/v1/supervisor/{supervisor_id}/status", router
    )
    if status == 200 and isinstance(body, dict):
        return body.get("payload", {})
    return None


def _action(supervisor_id: str, verb: str, router: str) -> str:
    status, body = _request(
        "POST", f"/druid/indexer/v1/supervisor/{supervisor_id}/{verb}", router
    )
    if status is None:
        return f"{supervisor_id}: unreachable ({body})"
    if status == 404:
        return f"{supervisor_id}: not registered"
    if status not in (200, 202):
        return f"{supervisor_id}: {verb} -> HTTP {status} {body}"
    return f"{supervisor_id}: {verb} ok"


def main():
    parser = argparse.ArgumentParser(description="Control Druid supervisors on demand")
    parser.add_argument("action", choices=["suspend", "resume", "wait", "status"])
    parser.add_argument("--router", default=DEFAULT_ROUTER)
    parser.add_argument("--ids", nargs="*", default=None)
    parser.add_argument("--timeout", type=int, default=300, help="Seconds to wait (wait)")
    parser.add_argument("--poll", type=int, default=5)
    parser.add_argument("--grace", type=int, default=15,
                        help="Seconds to ignore zero-lag before a resumed supervisor "
                             "has had time to observe new messages")
    args = parser.parse_args()

    ids = args.ids or load_ids()
    if not ids:
        print("No supervisor ids found.")
        return 0

    if args.action in ("suspend", "resume"):
        for supervisor_id in ids:
            print(_action(supervisor_id, args.action, args.router))
        return 0

    if args.action == "status":
        for supervisor_id in ids:
            payload = supervisor_status(supervisor_id, args.router) or {}
            print(
                f"{supervisor_id}: state={payload.get('state', 'unregistered')} "
                f"lag={payload.get('aggregateLag')} suspended={payload.get('suspended')}"
            )
        return 0

    # wait: require two consecutive drained reads so a just-resumed supervisor
    # has time to observe the new messages before we declare it caught up.
    started = time.time()
    deadline = started + args.timeout
    stable = 0
    while time.time() < deadline:
        payloads = [supervisor_status(i, args.router) for i in ids]
        if drained(payloads) and time.time() - started >= args.grace:
            stable += 1
            if stable >= 2:
                print("All supervisors drained.")
                return 0
        else:
            stable = 0
        time.sleep(args.poll)
    print("Timed out waiting for supervisors to drain.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
