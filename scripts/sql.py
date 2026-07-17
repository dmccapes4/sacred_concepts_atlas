#!/usr/bin/env python3
"""Tiny sqlite CLI stand-in: python scripts/sql.py <db> "<sql>" [--json] or --file schema.sql"""

import json
import sqlite3
import sys


def main() -> int:
    args = sys.argv[1:]
    as_json = "--json" in args
    if as_json:
        args.remove("--json")
    db = args[0]
    con = sqlite3.connect(db)
    try:
        if args[1] == "--file":
            con.executescript(open(args[2]).read())
            con.commit()
            print(f"applied {args[2]} to {db}")
            return 0
        cur = con.execute(args[1])
        rows = cur.fetchall()
        if as_json:
            cols = [d[0] for d in cur.description]
            print(json.dumps([dict(zip(cols, r)) for r in rows], indent=1, ensure_ascii=False))
        else:
            for r in rows:
                print("|".join(str(c) for c in r))
        con.commit()
        return 0
    finally:
        con.close()


if __name__ == "__main__":
    sys.exit(main())
