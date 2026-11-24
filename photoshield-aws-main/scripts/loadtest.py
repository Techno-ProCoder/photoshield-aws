"""Load test: many concurrent sample scans to drive CPU up and trigger auto scaling.

Each request makes a server process a 6 MP photo (~0.5 s of CPU), upload the result
to S3 and write a row to RDS. Results are written to docs/results/loadtest.csv.

Usage: python scripts/loadtest.py http://<alb-dns> --threads 24 --minutes 15
"""
import argparse
import csv
import os
import re
import statistics
import threading
import time
import urllib.error
import urllib.request
from collections import Counter

SERVED_BY = re.compile(r"Served by <code>(i-[0-9a-f]+)</code>")


def worker(url, stop_at, results, lock):
    while time.time() < stop_at:
        started = time.time()
        instance, ok = None, False
        try:
            req = urllib.request.Request(url + "/sample", data=b"", method="POST")
            with urllib.request.urlopen(req, timeout=90) as resp:
                page = resp.read().decode("utf-8", "replace")
                ok = resp.status == 200
                match = SERVED_BY.search(page)
                instance = match.group(1) if match else None
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            ok = False
        with lock:
            results.append((started, time.time() - started, ok, instance))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--threads", type=int, default=24)
    parser.add_argument("--minutes", type=float, default=15)
    parser.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "..", "docs", "results", "loadtest.csv"))
    args = parser.parse_args()
    url = args.url.rstrip("/")

    results, lock = [], threading.Lock()
    start = time.time()
    stop_at = start + args.minutes * 60
    threads = [threading.Thread(target=worker, args=(url, stop_at, results, lock), daemon=True)
               for _ in range(args.threads)]
    for t in threads:
        t.start()
    print(f"{time.strftime('%H:%M:%S')} started {args.threads} threads for {args.minutes} min against {url}")

    reported = 0
    while any(t.is_alive() for t in threads):
        time.sleep(30)
        with lock:
            window = results[reported:]
            reported = len(results)
        if window:
            latencies = [r[1] for r in window if r[2]]
            failed = sum(1 for r in window if not r[2])
            servers = Counter(r[3] for r in window if r[3])
            p50 = statistics.median(latencies) if latencies else 0
            print(f"{time.strftime('%H:%M:%S')}  {len(window) / 30:5.1f} req/s  p50 {p50:5.2f}s  "
                  f"failed {failed:3d}  servers {len(servers)}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["epoch_start", "seconds", "ok", "instance"])
        writer.writerows(results)

    latencies = sorted(r[1] for r in results if r[2])
    failed = sum(1 for r in results if not r[2])
    servers = Counter(r[3] for r in results if r[3])
    print(f"\nTotal requests {len(results)}, failed {failed} ({100 * failed / max(len(results), 1):.2f}%)")
    if latencies:
        p95 = latencies[int(0.95 * (len(latencies) - 1))]
        print(f"Latency p50 {statistics.median(latencies):.2f}s, p95 {p95:.2f}s")
    print("Requests per instance:", dict(servers))
    print("Saved", os.path.abspath(args.out))


if __name__ == "__main__":
    main()
