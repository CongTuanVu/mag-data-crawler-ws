#!/usr/bin/env python3
"""Simple agent to fetch building pages and extract features into CSV.

Usage:
  python mag_agent.py --input buildings.txt

Saves raw HTML to `output_raw/` and extracted CSV to `output_csv/features.csv`.
"""
from __future__ import annotations
import argparse
import csv
import os
import re
import time
from pathlib import Path
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUTPUT_RAW = ROOT / "output_raw"
OUTPUT_CSV = ROOT / "output_csv"

WIKI_API_SEARCH = "https://en.wikipedia.org/w/api.php?action=query&list=search&srsearch={q}&format=json"
WIKI_BASE = "https://en.wikipedia.org/wiki/"


def ensure_dirs() -> None:
    OUTPUT_RAW.mkdir(parents=True, exist_ok=True)
    OUTPUT_CSV.mkdir(parents=True, exist_ok=True)


def wiki_search_first(query: str) -> str | None:
    url = WIKI_API_SEARCH.format(q=quote_plus(query))
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    j = r.json()
    hits = j.get("query", {}).get("search", [])
    if not hits:
        return None
    title = hits[0]["title"]
    return WIKI_BASE + title.replace(" ", "_")


def fetch_url(url: str) -> str:
    r = requests.get(url, timeout=20, headers={"User-Agent": "mag-data-crawler/1.0"})
    r.raise_for_status()
    return r.text


def slug(name: str) -> str:
    s = re.sub(r"[^0-9a-zA-Z-_]+", "_", name).strip("_")
    return s[:200]


def parse_infobox(html: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    info = {}
    # coordinates
    geo = soup.select_one(".geo")
    if geo and geo.text:
        parts = geo.text.split(';')
        try:
            info["latitude"] = parts[0].strip()
            info["longitude"] = parts[1].strip() if len(parts) > 1 else ""
        except Exception:
            info["latitude"] = geo.text.strip()
            info["longitude"] = ""
    # infobox rows
    table = soup.find("table", class_=lambda c: c and "infobox" in c)
    if table:
        for tr in table.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if th and td:
                key = " ".join(th.stripped_strings)
                val = " ".join(td.stripped_strings)
                info[key] = val
    return info


def extract_features(name: str, page_url: str, raw_html_path: Path) -> dict:
    html = raw_html_path.read_text(encoding="utf-8")
    info = parse_infobox(html)
    # map common fields
    height_m = ""
    height_ft = ""
    for k in list(info.keys()):
        lk = k.lower()
        if "height" in lk and not height_m:
            # extract meters and feet
            m = re.search(r"([0-9,.]+)\s*m", info[k])
            f = re.search(r"([0-9,.]+)\s*ft", info[k])
            if m:
                height_m = m.group(1).replace(',', '')
            if f:
                height_ft = f.group(1).replace(',', '')
    # location
    city = ""
    country = ""
    loc_keys = [k for k in info.keys() if "location" in k.lower() or "address" in k.lower()]
    if loc_keys:
        loc = info[loc_keys[0]]
        parts = [p.strip() for p in re.split(r",|;", loc) if p.strip()]
        if parts:
            country = parts[-1]
            if len(parts) >= 2:
                city = parts[-2]

    year_completed = ""
    for k in info.keys():
        if "completed" in k.lower() or "opening" in k.lower():
            year = re.search(r"(19|20)\d{2}", info[k])
            if year:
                year_completed = year.group(0)
                break

    architect = ""
    for k in info.keys():
        if "architect" in k.lower() or "architects" in k.lower():
            architect = info[k]
            break

    lat = info.get("latitude", "")
    lon = info.get("longitude", "")

    return {
        "name": name,
        "wiki_url": page_url,
        "latitude": lat,
        "longitude": lon,
        "city": city,
        "country": country,
        "height_m": height_m,
        "height_ft": height_ft,
        "year_completed": year_completed,
        "architect": architect,
        "raw_path": str(raw_html_path),
    }


def run(input_path: Path):
    ensure_dirs()
    names = [l.strip() for l in input_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    out_csv = OUTPUT_CSV / "features.csv"
    fieldnames = ["name", "wiki_url", "latitude", "longitude", "city", "country", "height_m", "height_ft", "year_completed", "architect", "raw_path"]
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for name in names:
            try:
                print(f"Processing: {name}")
                page = wiki_search_first(name)
                if not page:
                    print(f"  No wiki page found for {name}")
                    continue
                html = fetch_url(page)
                filename = slug(name) + ".html"
                raw_path = OUTPUT_RAW / filename
                raw_path.write_text(html, encoding="utf-8")
                features = extract_features(name, page, raw_path)
                writer.writerow({k: features.get(k, "") for k in fieldnames})
                # polite pause
                time.sleep(1)
            except Exception as e:
                print(f"  Error processing {name}: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to newline-delimited building names")
    args = parser.parse_args()
    run(Path(args.input))


if __name__ == "__main__":
    main()
