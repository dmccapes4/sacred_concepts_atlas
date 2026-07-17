#!/usr/bin/env python3
"""Zip helper — replaces system `unzip` so fresh hosts don't need it."""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path


def cmd_test(args: argparse.Namespace) -> int:
    path = Path(args.zip)
    if not path.is_file() or path.stat().st_size == 0:
        print(f"ERROR: missing or empty zip: {path}", file=sys.stderr)
        return 1
    with zipfile.ZipFile(path) as zf:
        bad = zf.testzip()
        if bad is not None:
            print(f"ERROR: corrupt member: {bad}", file=sys.stderr)
            return 1
    print("zip OK")
    return 0


def cmd_count(args: argparse.Namespace) -> int:
    suffix = args.suffix.lower()
    with zipfile.ZipFile(args.zip) as zf:
        n = sum(1 for name in zf.namelist()
                if name.lower().endswith(suffix) and not name.endswith("/"))
    print(f"{args.label}: {n}")
    if n < args.min:
        print(f"ERROR: expected >= {args.min} *{suffix} files", file=sys.stderr)
        return 1
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.zip) as zf:
        zf.extractall(dest)
    print(f"unpacked to {dest}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("test", help="integrity-check a zip")
    p.add_argument("zip")
    p.set_defaults(func=cmd_test)

    p = sub.add_parser("count", help="count members ending in a suffix")
    p.add_argument("zip")
    p.add_argument("--suffix", required=True)
    p.add_argument("--min", type=int, required=True)
    p.add_argument("--label", default="files")
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("extract", help="extract a zip to a directory")
    p.add_argument("zip")
    p.add_argument("dest")
    p.set_defaults(func=cmd_extract)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
