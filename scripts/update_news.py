#!/usr/bin/env python3

import argparse
import email.utils
import html
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / "fsd_europa_data.json"
FEEDS = (
    ("Not a Tesla App", "https://www.notateslaapp.com/rss"),
    ("Electrek", "https://electrek.co/feed/"),
    ("Teslarati", "https://www.teslarati.com/feed/"),
)
TESLA_TERMS = (
    "fsd",
    "full self-driving",
    "autopilot",
    "software update",
    "tesla app",
    "release notes",
)
EUROPE_TERMS = (
    "europe",
    "european",
    "eu ",
    "germany",
    "german",
    "deutschland",
    "netherlands",
    "denmark",
    "belgium",
    "lithuania",
    "estonia",
    "france",
    "italy",
    "spain",
    "switzerland",
    "austria",
    "norway",
    "sweden",
    "finland",
    "poland",
    "uk ",
    "united kingdom",
    "release notes",
    "version ",
)
COUNTRIES = {
    "germany": "DE",
    "deutschland": "DE",
    "netherlands": "NL",
    "denmark": "DK",
    "belgium": "BE",
    "lithuania": "LT",
    "estonia": "EE",
    "france": "FR",
    "italy": "IT",
    "spain": "ES",
    "switzerland": "CH",
    "austria": "AT",
    "norway": "NO",
    "sweden": "SE",
    "finland": "FI",
    "poland": "PL",
    "united kingdom": "GB",
}


def text(node, name):
    element = node.find(name)
    return (element.text or "").strip() if element is not None else ""


def clean(value):
    value = re.sub(r"<[^>]+>", " ", html.unescape(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def normalized_url(value):
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def published_date(value):
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def country_for(value):
    lowered = value.lower()
    for name, iso in COUNTRIES.items():
        if name in lowered:
            return iso
    return None


def article_summary(value):
    summary = re.sub(r"\s*(Read More|View Release Notes)\s*$", "", clean(value), flags=re.IGNORECASE)
    summary = re.sub(r"\s*The post .+ appeared first on .+\.\s*$", "", summary, flags=re.IGNORECASE)
    if len(summary) <= 420:
        return summary

    shortened = summary[:420].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{shortened}…"


def relevant(value):
    lowered = f" {value.lower()} "
    return any(term in lowered for term in TESLA_TERMS) and any(term in lowered for term in EUROPE_TERMS)


def fetch_feed(source, url, cutoff):
    request = urllib.request.Request(url, headers={"User-Agent": "FSD-Europa-News-Updater/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        root = ET.fromstring(response.read())

    items = []
    for node in root.findall("./channel/item"):
        title = clean(text(node, "title"))
        link = text(node, "link")
        description = clean(text(node, "description"))
        published = published_date(text(node, "pubDate"))
        searchable = f"{title} {description}"

        if not title or not link or not published or published < cutoff or not relevant(searchable):
            continue

        items.append(
            {
                "id": f"auto-{published:%Y%m%d}-{abs(hash(normalized_url(link))) % 10**10}",
                "title": title,
                "date": published.strftime("%Y-%m-%d"),
                "source": source,
                "summary": article_summary(description),
                "url": link,
                "countryISO": country_for(searchable),
                "sourceKind": "news",
            }
        )
    return items


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    payload = json.loads(DATA_PATH.read_text())
    existing_by_url = {
        normalized_url(item["url"]): item
        for item in payload.get("news", [])
        if item.get("url")
    }
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    additions = []
    refreshed = []

    for source, url in FEEDS:
        for item in fetch_feed(source, url, cutoff):
            key = normalized_url(item["url"])
            existing = existing_by_url.get(key)
            if existing is None:
                additions.append(item)
                existing_by_url[key] = item
            elif existing.get("summary", "").startswith("Automatisch gefundene Meldung"):
                existing["summary"] = item["summary"]
                refreshed.append(existing)

    if not additions and not refreshed:
        print("Keine neuen passenden Meldungen.")
        return

    if additions:
        additions.sort(key=lambda item: item["date"], reverse=True)
        print(f"{len(additions)} neue Meldung(en):")
        for item in additions:
            print(f"- {item['date']} {item['source']}: {item['title']}")

    if refreshed:
        print(f"{len(refreshed)} Platzhalter-Zusammenfassung(en) ersetzt.")

    if args.dry_run:
        return

    payload["news"] = sorted(
        additions + payload.get("news", []),
        key=lambda item: (item.get("date", ""), item.get("id", "")),
        reverse=True,
    )[:40]
    payload["lastUpdated"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    DATA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
