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
    # (site, url)
    ("The Race", "https://www.the-race.com/feed/"),
    ("The Race", "https://www.the-race.com/category/formula-1/feed/"),
    ("The Race", "https://www.the-race.com/formula-1/feed/"),
    ("The Race", "https://www.the-race.com/category/wec/feed/"),
    ("The Race", "https://www.the-race.com/category/imsa/feed/"),
    ("Sportscar365", "https://sportscar365.com/feed/"),
    ("RACER", "https://racer.com/feed/"),
    ("TouringCarTimes", "https://www.touringcartimes.com/feed/"),
    ("Motorsport Week", "https://www.motorsportweek.com/feed/"),
    ("GT Report", "https://www.gt-report.com/feed/"),
    ("Dailysportscar", "https://www.dailysportscar.com/feed"),
    ("Dailysportscar", "https://www.dailysportscar.com/rss"),
    ("Speedcafe", "https://speedcafe.com/feed/"),
    ("Dive Bomb", "https://www.dive-bomb.com/feed"),
    ("NLS (official)", "https://www.nuerburgring-langstrecken-serie.de/feed/"),
    ("BTCC (official)", "https://www.btcc.net/feed/"),
    ("IMSA (official)", "https://www.imsa.com/feed/"),
    ("FIA WEC (official)", "https://www.fiawec.com/en/rss"),
    ("FIA (official)", "https://www.fia.com/rss/news"),
    ("Formula 1 (official)", "https://www.formula1.com/en/latest/all.xml"),
    ("Autosport", "https://www.autosport.com/rss/feed/f1"),
    ("Autosport", "https://www.autosport.com/rss/btcc/news/"),
    ("Autosport", "https://www.autosport.com/rss/wec/news/"),
    ("Autosport", "https://www.autosport.com/rss/imsa/news/"),
    ("Motorsport.com", "https://www.motorsport.com/rss/f1/news/"),
    ("Motorsport.com", "https://www.motorsport.com/rss/wec/news/"),
]

TERMS: list[tuple[str, str]] = [
    ("The Race", "https://www.the-race.com/terms-and-conditions/"),
    ("The Race", "https://www.the-race.com/terms-of-use/"),
    ("Sportscar365", "https://sportscar365.com/terms-of-use/"),
    ("Sportscar365", "https://sportscar365.com/terms/"),
    ("RACER", "https://racer.com/terms-of-use/"),
    ("TouringCarTimes", "https://www.touringcartimes.com/terms-and-conditions/"),
    ("Motorsport Week", "https://www.motorsportweek.com/terms-and-conditions/"),
    ("GT Report", "https://www.gt-report.com/terms-and-conditions/"),
    ("Dailysportscar", "https://www.dailysportscar.com/terms-and-conditions"),
    ("Speedcafe", "https://speedcafe.com/terms-and-conditions/"),
    ("Autosport", "https://www.autosport.com/info/terms-of-use/"),
    ("Motorsport.com", "https://www.motorsport.com/info/terms-of-use/"),
    ("Formula 1", "https://www.formula1.com/en/information/legal-notices.7egvZU48hzrypubGBNcQKt"),
]

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
    out |= {"items": len(entries), "newest": build.iso(max(times)) if times else None,
            "samples": [build.clean_title(e["title"]) for e in entries[:2]], "images": has_images(body)}
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


def main() -> int:
    feeds = [check_feed(s, u) for s, u in FEEDS]
    terms = [check_terms(s, u) for s, u in TERMS]
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
