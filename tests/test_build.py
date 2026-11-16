"""Run with: python3 -m unittest discover -s tests"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


def multi_series_feed() -> dict:
    return {"id": "ex", "name": "Example", "url": "https://example.com/feed/", "series": []}


class FeedParsing(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = build.load_config()
        self.cfg.photos = []
        self.matcher = build.Matcher(self.cfg)

    def items(self, fixture: str, feed: dict) -> list[dict]:
        entries = build.parse_feed((FIXTURES / fixture).read_bytes())
        return build.items_for_feed(feed, entries, {}, self.cfg, self.matcher, NOW)

    def test_rss_keeps_only_headline_source_time_link(self) -> None:
        items = self.items("wordpress.rss", multi_series_feed())
        first = items[0]
        self.assertEqual(set(first), {"title", "source", "time", "url", "series", "circuit", "photo"})
        self.assertEqual(first["title"], "IMSA confirms ‘Petit Le Mans’ date for 2027")
        self.assertEqual(first["url"], "https://example.com/imsa-petit?id=7")  # utm_ dropped
        self.assertEqual(first["time"], "2026-11-13T10:15:00Z")
        self.assertEqual(first["source"], "Example")

    def test_series_and_circuit_detection(self) -> None:
        by_url = {it["url"]: it for it in self.items("wordpress.rss", multi_series_feed())}
        self.assertEqual(by_url["https://example.com/imsa-petit?id=7"]["series"], ["imsa"])
        self.assertIsNone(by_url["https://example.com/imsa-petit?id=7"]["circuit"])  # Petit Le Mans is not Le Mans
        self.assertEqual(by_url["https://example.com/btcc-test"]["series"], ["btcc"])
        self.assertEqual(by_url["https://example.com/btcc-test"]["circuit"], "snetterton")

    def test_series_from_category_tags(self) -> None:
        by_url = {it["url"]: it for it in self.items("wordpress.rss", multi_series_feed())}
        self.assertEqual(by_url["https://example.com/barwell"]["series"], ["british-gt"])  # headline names no series

    def test_drops_unmatched_old_and_unsafe(self) -> None:
        urls = {it["url"] for it in self.items("wordpress.rss", multi_series_feed())}
        self.assertNotIn("https://example.com/nascar", urls)  # no series we cover
        self.assertNotIn("https://example.com/old", urls)     # older than MAX_AGE_DAYS
        self.assertFalse(any("javascript" in u for u in urls))

    def test_undated_item_uses_first_seen(self) -> None:
        by_url = {it["url"]: it for it in self.items("wordpress.rss", multi_series_feed())}
        self.assertEqual(by_url["https://example.com/nodate"]["time"], "2026-11-13T12:00:00Z")

    def test_atom_and_fixed_series(self) -> None:
        feed = multi_series_feed() | {"series": ["f1"]}
        [item] = self.items("atom.xml", feed)
        self.assertEqual(item["title"], "Verstappen quickest in F1 practice at Monza")
        self.assertEqual(item["url"], "https://example.org/f1/monza")
        self.assertEqual(item["series"], ["f1"])
        self.assertEqual(item["circuit"], "monza")


class Helpers(unittest.TestCase):
    def test_junior_formulas_tagged_separately(self) -> None:
        matcher = build.Matcher(build.load_config())
        self.assertEqual(matcher.series_in("Sprint win in F2 at Monza"), ["f2"])
        self.assertEqual(matcher.series_in("FIA Formula 3 champion steps up"), ["f3"])

    def test_acronyms_are_case_sensitive(self) -> None:
        pat = build.keyword_pattern(["WEC", "Le Mans"])
        self.assertTrue(pat.search("WEC title fight"))
        self.assertFalse(pat.search("the wec of it"))
        self.assertTrue(pat.search("LE MANS winner"))

    def test_calendar_only_series_never_matches_headlines(self) -> None:
        cfg = build.load_config()
        cfg.series["calendar-only"] = {"id": "calendar-only", "name": "X", "keywords": []}
        self.assertEqual(build.Matcher(cfg).series_in("Anything at all"), [])

    def test_whole_words_only(self) -> None:
        self.assertFalse(build.keyword_pattern(["Spa"]).search("Spanish GP"))

    def test_title_cleaning(self) -> None:
        self.assertEqual(build.clean_title("  A <b>bold</b>\n move &amp; more "), "A bold move & more")
        self.assertEqual(len(build.clean_title("x" * 500)), build.MAX_TITLE_CHARS)


class Validation(unittest.TestCase):
    def test_repo_data_is_valid(self) -> None:
        self.assertEqual(build.validate(build.load_config()), [])

    def test_bad_event_is_reported(self) -> None:
        cfg = build.load_config()
        cfg.calendars = [{"_path": "data/calendar/2027/btcc.json", "series": "btcc", "year": 2027, "events": [
            {"round": 1, "name": "Donington", "circuit": "nowhere", "start": "2027-04-11",
             "end": "2027-04-10", "status": "maybe", "sessions": [{"name": "Race 1", "start": "11:00"}]}]}]
        errors = "\n".join(build.validate(cfg))
        for expected in ("unknown circuit", "end is before start", "status must be", "UTC start"):
            self.assertIn(expected, errors)

    def test_tbc_event_may_omit_round_circuit_and_dates(self) -> None:
        cfg = build.load_config()
        cfg.calendars = [{"_path": "data/calendar/2027/btcc.json", "series": "btcc", "year": 2027, "events": [
            {"round": None, "name": "Round to be announced", "circuit": None, "start": None, "end": None, "status": "tbc"},
            {"name": "One-off race", "circuit": "spa", "start": "2027-06-01", "end": "2027-06-02", "status": "confirmed"}]}]
        self.assertEqual(build.validate(cfg), [])
        events = build.build_calendar(cfg, NOW)["events"]
        self.assertEqual([e["name"] for e in events], ["One-off race", "Round to be announced"])  # undated last

    def test_only_tbc_events_may_skip_dates(self) -> None:
        cfg = build.load_config()
        cfg.calendars = [{"_path": "data/calendar/2027/btcc.json", "series": "btcc", "year": 2027, "events": [
            {"round": 1, "name": "Donington", "circuit": None, "start": None, "end": None, "status": "provisional"}]}]
        errors = "\n".join(build.validate(cfg))
        self.assertIn("missing circuit", errors)
        self.assertIn("start and end must be", errors)

    def test_denied_feed_cannot_be_enabled(self) -> None:
        cfg = build.load_config()
        cfg.feeds = [multi_series_feed() | {"home": "https://example.com", "enabled": True, "permission": "denied"}]
        self.assertIn("permission is denied", "\n".join(build.validate(cfg)))


class Output(unittest.TestCase):
    def test_news_dedupes_and_sorts_newest_first(self) -> None:
        cfg = build.load_config()
        item = {"title": "Same story", "source": "A", "url": "https://a/1", "series": ["f1"], "circuit": None, "photo": None}
        state = {
            "a": {"items": [item | {"time": "2026-11-13T09:00:00Z"}]},
            "b": {"items": [item | {"source": "B", "url": "https://b/1", "time": "2026-11-13T08:00:00Z"},
                            item | {"title": "Newer", "url": "https://b/2", "time": "2026-11-13T11:00:00Z"}]},
        }
        news = build.build_news(cfg, state, NOW)
        self.assertEqual([it["url"] for it in news["items"]], ["https://b/2", "https://a/1"])
        self.assertEqual(news["schema_version"], build.SCHEMA_VERSION)

    def test_items_that_left_the_feed_are_kept_for_max_age(self) -> None:
        base = {"source": "A", "series": ["nls"], "circuit": None, "photo": None}
        fresh = [base | {"title": "New", "url": "https://a/new", "time": "2026-11-13T11:00:00Z"}]
        kept = [base | {"title": "Gone from feed", "url": "https://a/old", "time": "2026-11-05T10:00:00Z"},
                base | {"title": "Too old", "url": "https://a/ancient", "time": "2026-10-20T10:00:00Z"},
                base | {"title": "New (stale copy)", "url": "https://a/new", "time": "2026-11-13T11:00:00Z"}]
        merged = build.merge_items(fresh, kept, NOW)
        self.assertEqual([it["title"] for it in merged], ["New", "Gone from feed"])

    def test_no_per_series_cap(self) -> None:
        cfg = build.load_config()
        base = {"source": "A", "series": ["f1"], "circuit": None, "photo": None}
        state = {"a": {"items": [base | {"title": f"Story {i}", "url": f"https://a/{i}",
                                         "time": f"2026-11-13T{i % 10:02d}:{i // 10:02d}:00Z"} for i in range(60)]}}
        self.assertEqual(len(build.build_news(cfg, state, NOW)["items"]), 60)

    def test_cached_items_of_removed_series_are_dropped(self) -> None:
        cfg = build.load_config()
        base = {"source": "A", "time": "2026-11-13T09:00:00Z", "circuit": None, "photo": None}
        state = {"a": {"items": [base | {"title": "Old IndyCar story", "url": "https://a/1", "series": ["indycar"]},
                                 base | {"title": "Mixed story", "url": "https://a/2", "series": ["indycar", "imsa"]}]}}
        items = build.build_news(cfg, state, NOW)["items"]
        self.assertEqual([(it["url"], it["series"]) for it in items], [("https://a/2", ["imsa"])])


if __name__ == "__main__":
    unittest.main()
