"""Build data/seed.json from public Binance BTCUSDT 1-minute klines.

Build-time helper only; the bot never imports it and never touches the network.
Run: python tools/make_seed.py
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone

URL = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=1m&limit=1000&startTime={}"
START_MS = int(datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
# median cbBTC/USDC Orca close over Binance close on the kit's dev window (about -7.4 bps)
BASIS = 0.99926
BAR_MS = 60_000
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed.json")


def fetch(start_ms):
    req = urllib.request.Request(URL.format(start_ms), headers={"User-Agent": "cbbtc-sunday-rider-seed/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    now_ms = int(time.time() * 1000)
    ts, cs = [], []
    start = START_MS
    while start < now_ms:
        rows = fetch(start)
        if not rows:
            break
        for row in rows:
            close_t = int(row[0]) + BAR_MS
            # drop the still-open minute
            if close_t > now_ms:
                continue
            if ts and close_t <= ts[-1]:
                continue
            ts.append(close_t)
            cs.append(int(round(float(row[4]) * BASIS * 100000)))
        start = int(rows[-1][0]) + BAR_MS
        if len(rows) < 1000:
            break
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    seed = {"source": "Binance BTCUSDT 1m klines (data-api.binance.vision) x basis", "basis": BASIS, "t": ts, "c": cs}
    with open(OUT, "w", encoding="ascii", newline="\n") as f:
        f.write(json.dumps(seed, separators=(",", ":")) + "\n")
    print(f"wrote {len(ts)} bars to {OUT}")
    if ts:
        fmt = lambda ms: datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        print(f"first close {fmt(ts[0])}, last close {fmt(ts[-1])}")


if __name__ == "__main__":
    main()
