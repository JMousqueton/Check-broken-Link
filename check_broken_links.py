#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Multi-threaded Internal Link Checker with Real-time and Final CSV Export

Features:
- Crawls all internal links from a base URL up to a specified depth
- Detects HTTP 200, 400, 404, 500 and other request errors
- Displays dynamic progress in terminal with rich
- Supports exporting broken links at end or in real-time to CSV (with source page that linked to the broken page)

Usage:
  python check_broken_links.py -u https://example.com --export broken.csv
  python check_broken_links.py -u https://example.com --export-realtime live_broken.csv

Author: Julien Mousqueton
License: MIT
"""

import argparse
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, urldefrag
from collections import defaultdict, deque
from rich.console import Console
from rich.table import Table
from rich.live import Live
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import csv

# Shared state
visited = set()
discovered = set()
broken = []  # tuples: (target_url, error, source_url)
stats = defaultdict(int)
lock = threading.Lock()
realtime_export_file = None
realtime_export_lock = threading.Lock()
console = Console()

def normalize_url(base, link):
    joined = urljoin(base, link)
    clean, _ = urldefrag(joined)
    return clean.strip().rstrip("/")

def is_internal(base_url, url):
    return urlparse(url).netloc == urlparse(base_url).netloc

def build_table():
    in_queue = len(discovered - visited)
    table = Table(title="📊 Link Checking Progress")
    table.add_column("Metric", style="bold")
    table.add_column("Count", justify="right")
    table.add_row("Links Discovered", str(len(discovered)))
    table.add_row("Links Checked", str(len(visited)))
    table.add_row("🔁 In Queue", str(in_queue))
    table.add_row("✅ 200 OK", str(stats[200]))
    table.add_row("⚠️ 400 Bad Request", str(stats[400]))
    table.add_row("❌ 404 Not Found", str(stats[404]))
    table.add_row("🔥 500 Server Error", str(stats[500]))
    table.add_row("⚠️ Other Errors", str(stats['error']))
    return table

def export_row_realtime(error, target_url, source_url):
    global realtime_export_file
    if not realtime_export_file:
        return
    with realtime_export_lock:
        writer = csv.writer(realtime_export_file)
        writer.writerow([error, target_url, source_url])
        realtime_export_file.flush()

def fetch_and_extract(url, base_url, depth, max_depth, source_url):
    result_links = []

    with lock:
        if url in visited or depth > max_depth:
            return []
        visited.add(url)

    try:
        response = requests.get(url, timeout=10)
        status = response.status_code

        with lock:
            stats[status] += 1

        if status >= 400:
            with lock:
                broken.append((url, status, source_url))
                export_row_realtime(status, url, source_url)
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        for tag in soup.find_all("a", href=True):
            href = normalize_url(url, tag['href'])
            if href.startswith("mailto:") or href.startswith("tel:"):
                continue
            if is_internal(base_url, href):
                with lock:
                    if href not in discovered:
                        discovered.add(href)
                        result_links.append((href, depth + 1, url))

    except requests.RequestException as e:
        with lock:
            stats["error"] += 1
            broken.append((url, str(e), source_url))
            export_row_realtime("ERROR", url, source_url)

    return result_links

def crawl_concurrent(base_url, max_depth, max_workers=10):
    queue = deque([(base_url, 0, "root")])
    discovered.add(base_url)

    with Live(build_table(), refresh_per_second=4, console=console) as live:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            while queue:
                futures = {}
                while queue:
                    url, depth, source = queue.popleft()
                    future = executor.submit(fetch_and_extract, url, base_url, depth, max_depth, source)
                    futures[future] = (url, depth, source)

                for future in as_completed(futures):
                    result = future.result()
                    if result:
                        queue.extend(result)
                    live.update(build_table())

def print_error_summary():
    if not broken:
        console.print("✅ No broken links found.")
        return

    table = Table(title="❌ Broken Links Summary (400, 404, 500, etc.)")
    table.add_column("Error", style="bold red")
    table.add_column("URL", style="dim", overflow="fold")
    table.add_column("Source", style="cyan", overflow="fold")

    for url, err, source in broken:
        err_text = str(err) if isinstance(err, int) else "ERROR"
        table.add_row(err_text, url, source)

    console.print(table)

def export_errors_to_csv(filename):
    with open(filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Error", "URL", "Source"])

        for url, err, source in broken:
            if isinstance(err, int) and err in (400, 404, 500):
                writer.writerow([err, url, source])
            elif isinstance(err, str):
                writer.writerow(["ERROR", url, source])

    console.print(f"💾 Exported broken links to [bold green]{filename}[/bold green]")

def main():
    parser = argparse.ArgumentParser(description="Multithreaded internal link checker with export.")
    parser.add_argument("-u", "--url", required=True, help="Base URL to start crawling from")
    parser.add_argument("-d", "--depth", type=int, default=5, help="Maximum crawl depth (default: 5)")
    parser.add_argument("-t", "--threads", type=int, default=10, help="Number of threads (default: 10)")
    parser.add_argument("-e", "--export", type=str, help="Export broken links to CSV file after scan")
    parser.add_argument("--export-realtime", type=str, help="Export broken links to CSV file in real time")
    args = parser.parse_args()

    base_url = normalize_url(args.url, "")
    console.print(f"🌐 Scanning [bold blue]{base_url}[/bold blue] up to depth [bold]{args.depth}[/bold] with [bold]{args.threads}[/bold] threads\n")

    global realtime_export_file
    if args.export_realtime:
        realtime_export_file = open(args.export_realtime, "w", newline="", encoding="utf-8")
        writer = csv.writer(realtime_export_file)
        writer.writerow(["Error", "URL", "Source"])

    crawl_concurrent(base_url, args.depth, args.threads)

    console.print("\n✅ [bold green]Scan complete[/bold green]")
    print_error_summary()

    if args.export:
        export_errors_to_csv(args.export)

    if realtime_export_file:
        realtime_export_file.close()

if __name__ == "__main__":
    main()
