#!/usr/bin/env python3
"""Build news.json and calendar.json for the GTCC Manager Home screen.

Reads the hand-maintained files in data/, fetches the enabled news feeds,
keeps only headline, source, time and link, and writes the results to site/.
Standard library only, so the GitHub Action needs no installs.

Usage:
    python3 scripts/build.py            # fetch feeds, build site/
    python3 scripts/build.py --offline  # no network: validate data, rebuild from cached state
"""
from __future__ import annotations

import argparse
import email.utils
import html
import json
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SITE = ROOT / "site"
STATE_FILE = ROOT / "state" / "feeds.json"
STALE_REPORT = ROOT / "state" / "stale.md"

SCHEMA_VERSION = 1
USER_AGENT = "gtcc-feed/1.0"
FETCH_TIMEOUT_S = 20
MAX_ITEMS = 40            # items in news.json
MAX_ITEMS_PER_FEED = 10   # so one outlet can't fill the list
MAX_AGE_DAYS = 14         # older headlines are dropped
MAX_TITLE_CHARS = 200
STALE_AFTER_DAYS = 7      # an enabled feed failing this long is reported
PERMISSIONS = {"unknown", "required", "granted", "denied"}
EVENT_STATUSES = {"confirmed", "provisional", "cancelled"}
TRACKING_PARAMS = re.compile(r"^(utm_|fbclid$|gclid$|mc_)")

ATOM = "{http://www.w3.org/2005/Atom}"
DC = "{http://purl.org/dc/elements/1.1/}"
RSS1 = "{http://purl.org/rss/1.0/}"


# --------------------------------------------------------------------------- helpers

def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(text: str | None) -> datetime | None:
    """RSS pubDate (RFC 822) or Atom/ISO 8601 -> aware UTC datetime."""
    if not text:
        return None
    text = text.strip()
    try:
        dt = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        dt = None
    if dt is None:
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def clean_title(raw: str | None) -> str:
    """Strip markup and entities, collapse whitespace, cap the length."""
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = html.unescape(html.unescape(text))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > MAX_TITLE_CHARS:
        text = text[: MAX_TITLE_CHARS - 1].rstrip() + "\u2026"
    return text


def canonical_url(url: str | None) -> str | None:
    """http(s) only; drop tracking parameters and fragments."""
    if not url:
        return None
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if not TRACKING_PARAMS.match(k)]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path,
                                    urllib.parse.urlencode(query), ""))


def keyword_pattern(keywords: list[str]) -> re.Pattern[str]:
    """Whole-word match. All-caps keywords (acronyms) are case-sensitive."""
    parts = []
    for kw in keywords:
        body = re.escape(kw)
        parts.append(f"(?-i:{body})" if kw.isupper() else body)
    return re.compile(r"(?<![\w-])(?:" + "|".join(parts) + r")(?![\w-])", re.IGNORECASE)


# --------------------------------------------------------------------------- config

@dataclass
class Config:
    series: dict[str, dict[str, Any]]
    circuits: dict[str, dict[str, Any]]
    feeds: list[dict[str, Any]]
    photos: list[dict[str, Any]]
    calendars: list[dict[str, Any]]


def load_config() -> Config:
    series = {s["id"]: s for s in load_json(DATA / "series.json")["series"]}
    circuits = {c["id"]: c for c in load_json(DATA / "circuits.json")["circuits"]}
    feeds = load_json(DATA / "feeds.json")["feeds"]
    photos = load_json(DATA / "photos.json")["photos"]
    calendars = []
    for path in sorted((DATA / "calendar").glob("*/*.json")):
        cal = load_json(path)
        cal["_path"] = str(path.relative_to(ROOT))
        calendars.append(cal)
    return Config(series, circuits, feeds, photos, calendars)


def validate(cfg: Config) -> list[str]:
    """Every problem found; an empty list means the data can be published."""
    errors: list[str] = []

    seen: set[str] = set()
    for feed in cfg.feeds:
        fid = feed.get("id", "?")
        if fid in seen:
            errors.append(f"feeds.json: duplicate id {fid}")
        seen.add(fid)
        for key in ("id", "name", "url", "home"):
            if not feed.get(key):
                errors.append(f"feeds.json {fid}: missing {key}")
        if feed.get("permission") not in PERMISSIONS:
            errors.append(f"feeds.json {fid}: permission must be one of {sorted(PERMISSIONS)}")
        if feed.get("enabled") and feed.get("permission") == "denied":
            errors.append(f"feeds.json {fid}: enabled but permission is denied")
        for s in feed.get("series", []):
            if s not in cfg.series:
                errors.append(f"feeds.json {fid}: unknown series {s}")
        if feed.get("url") and not canonical_url(feed["url"]):
            errors.append(f"feeds.json {fid}: url must be http(s)")

    for photo in cfg.photos:
        pid = photo.get("id", "?")
        for key in ("id", "file", "author", "licence", "licence_url", "source_url"):
            if not photo.get(key):
                errors.append(f"photos.json {pid}: missing {key} (credit is required)")
        if photo.get("file") and not (DATA / "photos" / photo["file"]).is_file():
            errors.append(f"photos.json {pid}: file data/photos/{photo['file']} not found")
        for s in photo.get("series", []):
            if s not in cfg.series:
                errors.append(f"photos.json {pid}: unknown series {s}")
        if photo.get("circuit") and photo["circuit"] not in cfg.circuits:
            errors.append(f"photos.json {pid}: unknown circuit {photo['circuit']}")

    for cal in cfg.calendars:
        where = cal["_path"]
        if cal.get("series") not in cfg.series:
            errors.append(f"{where}: unknown series {cal.get('series')}")
        expected = Path(where).stem
        if cal.get("series") != expected:
            errors.append(f"{where}: series should be {expected} (the file name)")
        if str(cal.get("year")) != Path(where).parent.name:
            errors.append(f"{where}: year should match its folder")
        rounds: set[int] = set()
        for i, ev in enumerate(cal.get("events", [])):
            tag = f"{where} event {i + 1}"
            for key in ("round", "name", "circuit", "start", "end", "status"):
                if ev.get(key) in (None, ""):
                    errors.append(f"{tag}: missing {key}")
            if ev.get("circuit") and ev["circuit"] not in cfg.circuits:
                errors.append(f"{tag}: unknown circuit {ev['circuit']} (add it to circuits.json)")
            if ev.get("status") and ev["status"] not in EVENT_STATUSES:
                errors.append(f"{tag}: status must be one of {sorted(EVENT_STATUSES)}")
            if ev.get("round") in rounds:
                errors.append(f"{tag}: duplicate round {ev['round']}")
            rounds.add(ev.get("round"))
            try:
                start, end = date.fromisoformat(ev["start"]), date.fromisoformat(ev["end"])
                if end < start:
                    errors.append(f"{tag}: end is before start")
                if start.year != cal.get("year"):
                    errors.append(f"{tag}: start is not in {cal.get('year')}")
            except (KeyError, TypeError, ValueError):
                errors.append(f"{tag}: start and end must be YYYY-MM-DD")
            for s in ev.get("sessions", []):
                t = s.get("start", "")
                if not s.get("name") or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?Z", t):
                    errors.append(f"{tag}: each session needs a name and a UTC start like 2027-04-10T09:30:00Z")
    return errors


# --------------------------------------------------------------------------- feeds

def parse_feed(body: bytes) -> list[dict[str, Any]]:
    """RSS 2.0 or Atom -> [{title, url, time}] in feed order."""
    root = ET.fromstring(body.lstrip())  # some feeds send whitespace before <?xml
    entries: list[dict[str, Any]] = []
    if root.tag == f"{ATOM}feed":
        for e in root.findall(f"{ATOM}entry"):
            link = None
            for l in e.findall(f"{ATOM}link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href")
                    break
            when = e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated")
            entries.append({"title": e.findtext(f"{ATOM}title"), "url": link, "time": when})
    else:
        for ns in ("", RSS1):  # RSS 2.0 items are un-namespaced, RSS 1.0 (RDF) items are not
            for e in root.iter(f"{ns}item"):
                when = e.findtext("pubDate") or e.findtext(f"{DC}date")
                entries.append({"title": e.findtext(f"{ns}title"), "url": e.findtext(f"{ns}link"), "time": when})
    return entries


def fetch(feed: dict[str, Any], st: dict[str, Any]) -> bytes | None:
    """GET with conditional headers. None means 304 Not Modified."""
    req = urllib.request.Request(feed["url"], headers={"User-Agent": USER_AGENT})
    if st.get("etag"):
        req.add_header("If-None-Match", st["etag"])
    if st.get("last_modified"):
        req.add_header("If-Modified-Since", st["last_modified"])
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
            st["etag"] = resp.headers.get("ETag")
            st["last_modified"] = resp.headers.get("Last-Modified")
            return resp.read()
    except urllib.error.HTTPError as err:
        if err.code == 304:
            return None
        raise


class Matcher:
    def __init__(self, cfg: Config) -> None:
        self.series = [(sid, keyword_pattern(s["keywords"])) for sid, s in cfg.series.items()]
        self.circuits = [(cid, keyword_pattern(c["keywords"])) for cid, c in cfg.circuits.items()]

    def series_in(self, title: str) -> list[str]:
        return [sid for sid, pat in self.series if pat.search(title)]

    def circuit_in(self, title: str) -> str | None:
        return next((cid for cid, pat in self.circuits if pat.search(title)), None)


def pick_photo(cfg: Config, series: list[str], circuit: str | None) -> str | None:
    """A credited photo for the circuit, else for the series; None = the game shows its badge."""
    if circuit:
        for p in cfg.photos:
            if p.get("circuit") == circuit:
                return p["id"]
    for p in cfg.photos:
        if set(p.get("series", [])) & set(series):
            return p["id"]
    return None


def items_for_feed(feed: dict[str, Any], entries: list[dict[str, Any]], st: dict[str, Any],
                   cfg: Config, matcher: Matcher, now: datetime) -> list[dict[str, Any]]:
    first_seen: dict[str, str] = st.setdefault("first_seen", {})
    items = []
    for e in entries:
        title, url = clean_title(e["title"]), canonical_url(e["url"])
        if not title or not url:
            continue
        when = parse_time(e["time"])
        if when is None:
            when = parse_time(first_seen.get(url)) or now
        first_seen.setdefault(url, iso(when))
        when = min(when, now)  # a future-dated item would sit at the top forever
        if when < now - timedelta(days=MAX_AGE_DAYS):
            continue
        series = list(feed.get("series") or matcher.series_in(title))
        if not series:
            continue  # multi-series feed, nothing we cover
        circuit = matcher.circuit_in(title)
        items.append({
            "title": title,
            "source": feed["name"],
            "time": iso(when),
            "url": url,
            "series": series,
            "circuit": circuit,
            "photo": pick_photo(cfg, series, circuit),
        })
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
    # forget first-seen times for links no longer in the feed
    current = {canonical_url(e["url"]) for e in entries}
    st["first_seen"] = {u: t for u, t in first_seen.items() if u in current}
    return items


def update_feeds(cfg: Config, state: dict[str, Any], now: datetime, offline: bool) -> list[str]:
    """Refresh each enabled feed's cached items in state. Returns stale-feed lines."""
    matcher = Matcher(cfg)
    stale = []
    enabled_ids = {f["id"] for f in cfg.feeds if f.get("enabled")}
    for fid in list(state):
        if fid not in enabled_ids:
            del state[fid]  # disabled or removed: its headlines go at once
    for feed in cfg.feeds:
        if not feed.get("enabled"):
            continue
        st = state.setdefault(feed["id"], {})
        if not offline:
            try:
                body = fetch(feed, st)
                if body is not None:
                    st["items"] = items_for_feed(feed, parse_feed(body), st, cfg, matcher, now)
                st["last_ok"] = iso(now)
                st.pop("last_error", None)
                st.pop("stale_since", None)
            except Exception as err:  # keep the last good items
                st["last_error"] = f"{type(err).__name__}: {err}"[:300]
                st.setdefault("stale_since", iso(now))
                print(f"warning: {feed['id']}: {st['last_error']}", file=sys.stderr)
        since = parse_time(st.get("stale_since"))
        if since and now - since >= timedelta(days=STALE_AFTER_DAYS):
            stale.append(f"- `{feed['id']}` ({feed['url']}) failing since {st['stale_since']}: {st.get('last_error', '')}")
    return stale


def build_news(cfg: Config, state: dict[str, Any], now: datetime) -> dict[str, Any]:
    cutoff = now - timedelta(days=MAX_AGE_DAYS)
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    items = []
    pool = [it for fid in sorted(state) for it in state[fid].get("items", [])]
    pool.sort(key=lambda it: (it["time"], it["url"]), reverse=True)
    for it in pool:
        key = re.sub(r"\W+", "", it["title"].lower())
        if it["url"] in seen_urls or key in seen_titles or parse_time(it["time"]) < cutoff:
            continue
        seen_urls.add(it["url"])
        seen_titles.add(key)
        items.append(it)
        if len(items) >= MAX_ITEMS:
            break
    return {"schema_version": SCHEMA_VERSION, "updated": iso(now), "items": items,
            "photos": photo_credits(cfg, {it["photo"] for it in items if it["photo"]})}


# --------------------------------------------------------------------------- calendar

def build_calendar(cfg: Config, now: datetime) -> dict[str, Any]:
    events = []
    for cal in cfg.calendars:
        series = cfg.series[cal["series"]]
        for ev in cal.get("events", []):
            circuit = cfg.circuits[ev["circuit"]]
            sessions = sorted(ev.get("sessions", []), key=lambda s: s["start"])
            events.append({
                "series": series["id"],
                "series_name": series["name"],
                "year": cal["year"],
                "round": ev["round"],
                "name": ev["name"],
                "circuit": circuit["id"],
                "circuit_name": circuit["name"],
                "country": circuit["country"],
                "layout": ev.get("layout"),
                "start": ev["start"],
                "end": ev["end"],
                "status": ev["status"],
                "sessions": [{"name": s["name"], "start": s["start"]} for s in sessions],
                "photo": pick_photo(cfg, [series["id"]], circuit["id"]),
            })
    events.sort(key=lambda e: (e["start"], e["series"], e["round"]))
    used = {e["photo"] for e in events if e["photo"]}
    return {"schema_version": SCHEMA_VERSION, "updated": iso(now), "events": events,
            "photos": photo_credits(cfg, used)}


def photo_credits(cfg: Config, ids: set[str]) -> dict[str, Any]:
    out = {}
    for p in cfg.photos:
        if p["id"] in ids:
            out[p["id"]] = {"file": f"photos/{p['file']}", "title": p.get("title", ""),
                            "author": p["author"], "licence": p["licence"],
                            "licence_url": p["licence_url"], "source_url": p["source_url"]}
    return out


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offline", action="store_true", help="skip the network; validate and rebuild from cached state")
    args = ap.parse_args(argv)

    cfg = load_config()
    errors = validate(cfg)
    if errors:
        print("Data problems, nothing published:", *errors, sep="\n  ", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    state = load_json(STATE_FILE) if STATE_FILE.exists() else {}
    stale = update_feeds(cfg, state, now, args.offline)

    news = build_news(cfg, state, now)
    calendar = build_calendar(cfg, now)

    SITE.mkdir(exist_ok=True)
    write_json(SITE / "news.json", news)
    write_json(SITE / "calendar.json", calendar)
    photos_out = SITE / "photos"
    if photos_out.exists():
        shutil.rmtree(photos_out)
    photos_out.mkdir()
    for p in cfg.photos:
        shutil.copy2(DATA / "photos" / p["file"], photos_out / p["file"])
    write_json(STATE_FILE, state)

    if stale:
        STALE_REPORT.write_text("These enabled feeds have been failing for "
                                f"{STALE_AFTER_DAYS}+ days:\n\n" + "\n".join(stale) + "\n", encoding="utf-8")
    elif STALE_REPORT.exists():
        STALE_REPORT.unlink()

    print(f"news: {len(news['items'])} items from {sum(1 for f in cfg.feeds if f.get('enabled'))} enabled feeds; "
          f"calendar: {len(calendar['events'])} events; stale feeds: {len(stale)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
