#!/usr/bin/env python3
"""
List the source files that frozen research manifests pin by sha256, and verify them.

Editing, moving or reformatting any of these makes the dashboard mark the
matching research evidence stale ("Research source changed: ...") and makes the
frozen paper-trial observers refuse to run. Run --verify before and after changing
code under ml/evaluation, ml/feature_engineering, spark/jobs or poller/.

    python scripts/pinned_sources.py            # list every pinned path
    python scripts/pinned_sources.py --verify   # compare current file hashes to the manifests

--verify exits 1 when a pinned file's content no longer matches the hash recorded
in the manifests (so git history is irrelevant: only the bytes matter).
"""
import argparse
import glob
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pinned():
    """{repo-relative path: set of sha256 values recorded by manifests}."""
    found = {}

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "source_hashes" and isinstance(value, dict):
                    for name, digest in value.items():
                        # Bare names are relative to ml/evaluation/.
                        path = name if "/" in name else f"ml/evaluation/{name}"
                        found.setdefault(path, set()).add(digest)
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for name in glob.glob(str(ROOT / "ml/evaluation/results/**/*.json"), recursive=True):
        try:
            walk(json.loads(Path(name).read_text()))
        except (OSError, ValueError):
            continue
    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verify", action="store_true", help="check current hashes against the manifests")
    args = parser.parse_args()

    found = pinned()
    if not found:
        print("No research manifests found (ml/evaluation/results is empty); nothing is pinned here.")
        return 0
    if not args.verify:
        print("\n".join(sorted(found)))
        return 0
    bad = []
    for path, digests in sorted(found.items()):
        file = ROOT / path
        actual = hashlib.sha256(file.read_bytes()).hexdigest() if file.is_file() else None
        if actual not in digests:
            bad.append((path, "missing" if actual is None else "content differs from every recorded hash"))
    for path, why in bad:
        print(f"MISMATCH {path}: {why}")
    if bad:
        print(f"\n{len(bad)} of {len(found)} pinned file(s) no longer match: the matching research evidence reads as stale.")
        return 1
    print(f"All {len(found)} pinned files match their recorded hashes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
