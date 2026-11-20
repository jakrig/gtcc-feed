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
    # Round 3 candidates (2026-11-20): broader English-language motorsport outlets.
    ("The Checkered Flag", "https://www.thecheckeredflag.co.uk/feed/"),
    ("RaceFans", "https://www.racefans.net/feed/"),
    ("Motor Sport Magazine", "https://www.motorsportmagazine.com/feed/"),
    ("Crash.net", "https://www.crash.net/rss/f1"),
    ("Crash.net", "https://www.crash.net/rss/btcc"),
    ("Crash.net", "https://www.crash.net/rss/feed"),
    ("PlanetF1", "https://www.planetf1.com/feed"),
    ("RacingNews365", "https://racingnews365.com/feed/news.xml"),
    ("GPFans", "https://www.gpfans.com/en/rss.xml"),
    ("GPblog", "https://www.gpblog.com/en/rss/index.xml"),
    ("WTF1", "https://wtf1.com/feed/"),
    ("F1i", "https://f1i.com/feed"),
    ("GrandPrix247", "https://www.grandprix247.com/feed/"),
    ("F1Technical", "https://www.f1technical.net/rss/news.xml"),
    ("SnapLap", "https://snaplap.net/feed/"),
    ("Formula Rapida", "https://www.formularapida.net/feed"),
    ("Racecar Engineering", "https://www.racecar-engineering.com/feed/"),
    ("SPEED SPORT", "https://speedsport.com/feed/"),
    ("Motorsport UK (official)", "https://www.motorsportuk.org/feed/"),
    ("24 Hours of Le Mans (official)", "https://www.24h-lemans.com/en/rss"),
    ("Joe Saward", "https://joesaward.wordpress.com/feed/"),
]

# Terms pages are found by following "terms / legal / conditions / impressum" links on each homepage.
HOMES: list[tuple[str, str]] = [
    ("The Checkered Flag", "https://www.thecheckeredflag.co.uk/"),
    ("RaceFans", "https://www.racefans.net/"),
    ("Motor Sport Magazine", "https://www.motorsportmagazine.com/"),
    ("Crash.net", "https://www.crash.net/"),
    ("PlanetF1", "https://www.planetf1.com/"),
    ("RacingNews365", "https://racingnews365.com/"),
    ("GPFans", "https://www.gpfans.com/en/"),
    ("GPblog", "https://www.gpblog.com/en"),
    ("WTF1", "https://wtf1.com/"),
    ("F1i", "https://f1i.com/"),
    ("GrandPrix247", "https://www.grandprix247.com/"),
    ("F1Technical", "https://www.f1technical.net/"),
    ("SnapLap", "https://snaplap.net/"),
    ("Formula Rapida", "https://www.formularapida.net/"),
    ("Racecar Engineering", "https://www.racecar-engineering.com/"),
    ("SPEED SPORT", "https://speedsport.com/"),
    ("Motorsport UK (official)", "https://www.motorsportuk.org/"),
    ("24 Hours of Le Mans (official)", "https://www.24h-lemans.com/en"),
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
    found: list[tuple[int, str]] = []
    for href, text in re.findall(r'(?is)<a\b[^>]*href=["\']([^"\'#]+)["\'][^>]*>(.*?)</a>', body.decode("utf-8", "replace")):
        label = re.sub(r"<[^>]+>", " ", text).strip()
        url = urllib.parse.urljoin(final, href)
        path = urllib.parse.urlsplit(url).path
        if not url.startswith("http") or any(u == url for _, u in found):
            continue
        if TERMS_LINK.search(label) and len(label) < 40:
            found.append((0, url))  # footer link labelled "Terms", "Legal", ...
        elif TERMS_LINK.search(path) and len(path) < 45:
            found.append((1, url))  # short address like /terms-of-use, not a news article
    links = [u for _, u in sorted(found, key=lambda f: f[0])]
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
