#!/usr/bin/env python3
"""Query the literature index: filenames + indexed content.

    python3 query.py "maturity model"        # full-text hits
    python3 query.py --list                  # manifest table
"""
import argparse
import json
import re
import sys
from pathlib import Path

import config

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("text", nargs="?", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    a = ap.parse_args()

    mf = json.loads((config.STORE / "manifest.json").read_text()) \
        if (config.STORE / "manifest.json").exists() else {"docs": {}}
    docs = mf.get("docs", {})
    if a.list:
        for p, st in sorted(docs.items()):
            print(f"{st.get('status', '?'):>14}  {p}")
        print(f"{len(docs)} docs, watermark: {mf.get('watermark')}")
        return

    rx = re.compile(a.text, re.I)
    hits = 0
    for rel, st in sorted(docs.items()):
        if hits >= a.limit:
            break
        slug = config.slug(rel)
        for p in (config.STORE / "documents" / slug).glob("*.json"):
            txt = p.read_text()
            if rx.search(txt):
                m = rx.search(txt)
                snip = txt[max(0, m.start() - 120):m.end() + 120]
                snip = re.sub(r"\s+", " ", snip)
                print(f"\n=== {rel}  ({p.name})\n...{snip}...")
                hits += 1
    print(f"\n{hits} hits" if hits else "no matches")

if __name__ == "__main__":
    main()
