#!/usr/bin/env python3
"""Submit URLs changed by the last build (data/state/changed.txt) to IndexNow (Bing, Seznam, Yandex…)."""
import json
import os
import sys
import urllib.request
from pathlib import Path

APP = Path(__file__).resolve().parent.parent
KEY = os.environ.get("INDEXNOW_KEY", "")
HOST = "auty.cz"


def main():
    if not KEY:
        sys.exit("INDEXNOW_KEY missing")
    paths = [p for p in (APP / "data/state/changed.txt").read_text().split("\n") if p]
    urls = [f"https://{HOST}{p}" for p in paths]
    for i in range(0, len(urls), 10000):
        body = json.dumps({"host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt", "urlList": urls[i:i + 10000]}).encode()
        req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body, headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": "AutyBuild/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                print(f"IndexNow: {len(urls[i:i + 10000])} URLs -> HTTP {r.status}")
        except Exception as e:
            print(f"IndexNow: batch failed ({type(e).__name__})")


if __name__ == "__main__":
    main()
