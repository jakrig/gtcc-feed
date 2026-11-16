#!/usr/bin/env python3
"""Check candidate news feeds and the sites' terms pages, once, from CI.

For each candidate feed URL: HTTP status, content type, whether it parses as
RSS/Atom, item count, newest item date, two sample headlines, whether items
carry images, and the CORS header returned for a cross-site request. For each
terms page: the sentences that mention RSS, feeds, commercial use, linking,
headlines or scraping.

Writes a Markdown report to stdout and to $GITHUB_STEP_SUMMARY when set, and
JSON to site-check/feeds.json. One request per URL; nothing is stored beyond
the report.
"""
from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build  # noqa: E402  (reuse the real parser, so "parses" means the build can use it)

UA = "Mozilla/5.0 (compatible; gtcc-feed-check/1.0)"
ORIGIN = "https://jakrig.github.io"
TIMEOUT_S = 25

FEEDS: list[tuple[str, str]] = [
    # (site, url). Enabled feeds are listed again so the report shows their category tags.
    ("Sportscar365", "https://sportscar365.com/feed/"),
    ("RACER", "https://racer.com/feed/"),
    ("TouringCarTimes", "https://www.touringcartimes.com/feed/"),
    ("Motorsport Week", "https://www.motorsportweek.com/feed/"),
    ("GT Report", "https://www.gt-report.com/feed/"),
    ("Dailysportscar", "https://www.dailysportscar.com/feed"),
    ("Speedcafe", "https://speedcafe.com/feed/"),
    ("FIA", "https://www.fia.com/rss/news"),
    # NLS / Nürburgring 24h
    ("NLS (official)", "https://www.nuerburgring-langstrecken-serie.de/language/en/feed/"),
    ("NLS (official)", "https://www.nuerburgring-langstrecken-serie.de/language/de/feed/"),
    ("NLS (official)", "https://www.nuerburgring-langstrecken-serie.de/en/feed/"),
    ("ADAC 24h Nürburgring (official)", "https://www.24h-rennen.de/en/feed/"),
    ("ADAC 24h Nürburgring (official)", "https://www.24h-rennen.de/feed/"),
    ("Speedweek", "https://www.speedweek.com/en/rss"),
    ("Speedweek", "https://www.speedweek.com/rss"),
    ("GT Report", "https://www.gt-report.com/category/nls/feed/"),
    ("GT Report", "https://www.gt-report.com/category/24h-nurburgring/feed/"),
    # SRO: British GT, GT World Challenge, IGTC
    ("British GT (official)", "https://www.britishgt.com/rss"),
    ("British GT (official)", "https://www.britishgt.com/news/rss"),
    ("British GT (official)", "https://www.britishgt.com/feed"),
    ("GTWC Europe (official)", "https://www.gt-world-challenge-europe.com/rss"),
    ("GTWC Europe (official)", "https://www.gt-world-challenge-europe.com/news/rss"),
    ("GTWC America (official)", "https://www.gt-world-challenge-america.com/rss"),
    ("SRO (official)", "https://www.sro-motorsports.com/rss"),
    ("IGTC (official)", "https://www.intercontinentalgtchallenge.com/rss"),
    ("Pit Debrief", "https://pitdebrief.com/feed/"),
    ("Sportscar365", "https://sportscar365.com/sro/feed/"),
    ("Sportscar365", "https://sportscar365.com/sro/world-challenge-america/feed/"),
    ("Sportscar365", "https://sportscar365.com/sro/world-challenge-europe/feed/"),
    ("Sportscar365", "https://sportscar365.com/sro/british-gt/feed/"),
    ("Sportscar365", "https://sportscar365.com/imsa/feed/"),
    ("Sportscar365", "https://sportscar365.com/other-series/feed/"),
    ("Sportscar365", "https://sportscar365.com/other-series/creventic/feed/"),
    ("GT Report", "https://www.gt-report.com/category/british-gt/feed/"),
    ("GT Report", "https://www.gt-report.com/category/gt-world-challenge/feed/"),
    # 24H Series
    ("24H Series (official)", "https://www.24hseries.com/feed"),
    ("24H Series (official)", "https://www.24hseries.com/rss"),
    ("24H Series (official)", "https://www.24hseries.com/news/rss"),
    # BTCC and endurance
    ("TouringCars.net", "https://www.touringcars.net/feed/"),
    ("Endurance-Info", "https://www.endurance-info.com/en/feed"),
    ("Endurance-Info", "https://www.endurance-info.com/feed"),
    # F2 / F3
    ("FIA Formula 2 (official)", "https://www.fiaformula2.com/rss"),
    ("FIA Formula 3 (official)", "https://www.fiaformula3.com/rss"),
    ("Feeder Series", "https://www.feederseries.net/feed/"),
    ("Formula Scout", "https://www.formulascout.com/feed"),
    ("Formula Scout", "https://www.formulascout.com/feed/"),
    ("Motorsport Week", "https://www.motorsportweek.com/category/formula-2/feed/"),
]

# Terms pages are found by following "terms / legal / conditions / impressum" links on each homepage.
HOMES: list[tuple[str, str]] = [
    ("Sportscar365", "https://sportscar365.com/"),
    ("RACER", "https://racer.com/"),
    ("TouringCarTimes", "https://www.touringcartimes.com/"),
    ("Motorsport Week", "https://www.motorsportweek.com/"),
    ("GT Report", "https://www.gt-report.com/"),
    ("Dailysportscar", "https://www.dailysportscar.com/"),
    ("Speedcafe", "https://speedcafe.com/"),
    ("FIA", "https://www.fia.com/"),
    ("NLS (official)", "https://www.nuerburgring-langstrecken-serie.de/"),
    ("ADAC 24h Nürburgring (official)", "https://www.24h-rennen.de/en/"),
    ("Speedweek", "https://www.speedweek.com/en/"),
    ("British GT (official)", "https://www.britishgt.com/"),
    ("SRO (official)", "https://www.sro-motorsports.com/"),
    ("Pit Debrief", "https://pitdebrief.com/"),
    ("24H Series (official)", "https://www.24hseries.com/"),
    ("TouringCars.net", "https://www.touringcars.net/"),
    ("Endurance-Info", "https://www.endurance-info.com/"),
    ("Feeder Series", "https://www.feederseries.net/"),
    ("Formula Scout", "https://www.formulascout.com/"),
    ("FIA Formula 2 (official)", "https://www.fiaformula2.com/"),
]
TERMS_LINK = re.compile(r"terms|legal|conditions|condizioni|impressum|nutzungsbedingungen|agb|disclaimer|copyright|mentions", re.I)

TERMS_WORDS = re.compile(r"\b(rss|feeds?|syndicat\w*|commercial\w*|headlines?|hyperlink\w*|link(?:s|ing)?|scrap\w*|crawl\w*|aggregat\w*|framing|reproduc\w*)\b", re.I)
MEDIA = ("{http://search.yahoo.com/mrss/}content", "{http://search.yahoo.com/mrss/}thumbnail", "enclosure")


def get(url: str) -> tuple[int, dict[str, str], bytes, str]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Origin": ORIGIN,
                                               "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8, */*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read(3_000_000), resp.geturl()
    except urllib.error.HTTPError as err:
        return err.code, {k.lower(): v for k, v in err.headers.items()}, b"", url


def has_images(body: bytes) -> bool:
    try:
        root = ET.fromstring(body.lstrip())
    except ET.ParseError:
        return False
    for el in root.iter():
        if el.tag in MEDIA:
            return True
        if el.tag in ("description", "{http://purl.org/rss/1.0/modules/content/}encoded") and el.text and "<img" in el.text:
            return True
    return False


def check_feed(site: str, url: str) -> dict[str, Any]:
    out: dict[str, Any] = {"site": site, "url": url}
    try:
        status, headers, body, final = get(url)
    except Exception as err:  # DNS, TLS, timeout
        out["error"] = f"{type(err).__name__}: {err}"[:200]
        return out
    out |= {"status": status, "final_url": final, "content_type": headers.get("content-type", ""),
            "cors": headers.get("access-control-allow-origin"), "bytes": len(body)}
    if status != 200 or not body:
        return out
    try:
        entries = build.parse_feed(body)
    except ET.ParseError as err:
        out["parse_error"] = str(err)[:120]
        out["looks_like"] = "html" if b"<html" in body[:2000].lower() else "other"
        return out
    times = [t for t in (build.parse_time(e["time"]) for e in entries) if t]
    cats: dict[str, int] = {}
    for e in entries:
        for c in e.get("categories", []):
            cats[c] = cats.get(c, 0) + 1
    out |= {"items": len(entries), "newest": build.iso(max(times)) if times else None,
            "samples": [build.clean_title(e["title"]) for e in entries[:2]], "images": has_images(body),
            "categories": sorted(cats, key=lambda c: -cats[c])[:15]}
    return out


def text_of(body: bytes) -> str:
    raw = body.decode("utf-8", "replace")
    raw = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html.unescape(raw))


def check_terms(site: str, url: str) -> dict[str, Any]:
    out: dict[str, Any] = {"site": site, "url": url}
    try:
        status, _, body, final = get(url)
    except Exception as err:
        out["error"] = f"{type(err).__name__}: {err}"[:200]
        return out
    out |= {"status": status, "final_url": final}
    if status == 200 and body:
        sentences = re.split(r"(?<=[.;:])\s+", text_of(body))
        hits = [s.strip()[:400] for s in sentences if TERMS_WORDS.search(s) and 25 < len(s) < 1200]
        out["sentences"] = hits[:25]
    return out


def find_terms(site: str, home: str) -> list[dict[str, Any]]:
    """Follow up to three terms/legal links from the homepage."""
    try:
        status, _, body, final = get(home)
    except Exception as err:
        return [{"site": site, "url": home, "error": f"{type(err).__name__}: {err}"[:200]}]
    if status != 200:
        return [{"site": site, "url": home, "status": status}]
    links = []
    for href, text in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', body.decode("utf-8", "replace")):
        label = re.sub(r"<[^>]+>", " ", text)
        if TERMS_LINK.search(href) or TERMS_LINK.search(label):
            url = urllib.parse.urljoin(final, href)
            if url.startswith("http") and url not in links:
                links.append(url)
    if not links:
        return [{"site": site, "url": home, "status": status, "sentences": ["(no terms/legal link found on the homepage)"]}]
    return [check_terms(site, u) for u in links[:3]]


def main() -> int:
    feeds = [check_feed(s, u) for s, u in FEEDS]
    terms = [t for s, h in HOMES for t in find_terms(s, h)]
    Path("site-check").mkdir(exist_ok=True)
    Path("site-check/feeds.json").write_text(json.dumps({"feeds": feeds, "terms": terms}, ensure_ascii=False, indent=2))

    lines = ["## Feeds", "", "| Site | URL | HTTP | Feed? | Items | Newest | Images | CORS |", "|---|---|---|---|---|---|---|---|"]
    for f in feeds:
        ok = "yes" if "items" in f else (f.get("error") or f.get("parse_error") or f.get("looks_like") or "no")
        lines.append(f"| {f['site']} | {f['url']} | {f.get('status', '-')} | {ok} | {f.get('items', '')} | "
                     f"{f.get('newest') or ''} | {f.get('images', '')} | {f.get('cors') or 'none'} |")
    lines += ["", "### Sample headlines", ""]
    for f in feeds:
        if f.get("samples"):
            lines.append(f"- **{f['site']}** ({f['url']}): " + " / ".join(f["samples"]))
            if f.get("categories"):
                lines.append(f"  - categories: " + ", ".join(f["categories"]))
    lines += ["", "## Terms pages", ""]
    for t in terms:
        lines.append(f"### {t['site']}: {t['url']} (HTTP {t.get('status', t.get('error'))})")
        for s in t.get("sentences", []):
            lines.append(f"- {s}")
        lines.append("")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
