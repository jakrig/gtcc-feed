# gtcc-feed

Real-world racing news headlines and race calendars for the GTCC Manager Home screen.

Every hour a GitHub Action reads the approved news feeds, keeps only **headline, source, time and link**, merges in the hand-typed calendar, and publishes two small files to GitHub Pages:

- `https://jakrig.github.io/gtcc-feed/news.json`
- `https://jakrig.github.io/gtcc-feed/calendar.json`

The game reads only these files. It never contacts a racing site, so there are no CORS problems in the web build and no keys in the app.

## Layout

| Path | What it is | Who edits it |
| --- | --- | --- |
| `data/calendar/<year>/<series>.json` | Race weekends, typed from each series' official calendar | You |
| `data/feeds.json` | News sources, each switched on or off | You |
| `data/series.json` | Series ids, names, and headline keywords used to tag multi-series feeds. A series with no keywords (`"keywords": []`) is calendar-only and never appears in the news | You |
| `data/circuits.json` | Circuit ids, names, countries, headline keywords | You |
| `data/photos.json` + `data/photos/` | Openly licensed photos (e.g. Wikimedia Commons), with credits | You |
| `scripts/build.py` | The build (Python standard library only) | Code |
| `tests/` | Unit tests and sample feeds | Code |
| `site/` | The published output | The Action |
| `state/` | Feed cache: last good headlines, ETags, failures | The Action |

## Editing the calendar

One file per series per year. Add events like this:

```json
{
  "series": "btcc",
  "year": 2027,
  "source_url": "https://www.btcc.net/...the official calendar announcement...",
  "events": [
    {
      "round": 1,
      "name": "Donington Park",
      "circuit": "donington-park",
      "layout": "National",
      "start": "2027-04-10",
      "end": "2027-04-11",
      "status": "confirmed",
      "sessions": [
        {"name": "Race 1", "start": "2027-04-11T11:30:00Z"}
      ]
    }
  ]
}
```

- `circuit` must be an id from `data/circuits.json`; add the circuit there first if it's new.
- `status` is `confirmed`, `provisional`, `cancelled` or `tbc`. A `tbc` event is a placeholder for an announced round with no venue or date yet: `round`, `circuit`, `start` and `end` may be `null`. It is listed after the dated events.
- `round` is optional, for one-off events that aren't a championship round (e.g. the Nürburgring 24h in `nls`).
- `format` is optional free text shown to players: `sprint`, `24h`, `6h`, `3 races`, `Endurance Cup`.
- `notes` is optional and for maintainers only (disputed dates, what still needs checking). It is not published.
- Each race appears in **one** series only, its main one, so the game never lists it twice. For example the Spa 24 Hours lives in `gtwc-europe` and the Nürburgring 24h in `nls`, even though both also count for the Intercontinental GT Challenge.
- `sessions` is optional. Add it when the event timetable comes out. Times are **UTC** with a trailing `Z` (convert from the local time on the timetable).
- `layout` is optional free text.
- Type the dates yourself from the official announcement rather than copying another site's table.

Open a pull request: the Action runs the tests and validates every file. Merge it and the new calendar is live within minutes.

## Adding a news source

Add an entry to `data/feeds.json`:

```json
{"id": "the-race-f1", "name": "The Race", "url": "https://www.the-race.com/category/formula-1/feed/",
 "home": "https://www.the-race.com", "series": ["f1"], "enabled": true,
 "permission": "granted", "verified": true, "notes": "Email from ... on ..."}
```

- `name` is the source name shown in the game.
- `series` is the series every headline from this feed belongs to. Leave it `[]` for a feed that covers many series: each headline is then tagged from the keywords in `data/series.json`, and headlines that match no series are dropped.
- `permission` records where you stand: `unknown`, `required` (terms say written approval is needed), `granted` or `denied`. A feed with `denied` can't be enabled; the build refuses it.
- To remove a source, set `enabled` to `false`. Its headlines disappear on the next run.

Every feed in the list was checked from GitHub Actions on 2026-11-13 (`scripts/check_feeds.py`, run with the "Check feeds" workflow). Feeds whose terms forbid or restrict commercial use stay disabled until written permission arrives; Formula 1 is `denied` because its RSS terms forbid aggregation.

## Pictures

The feed never passes on article images (they are usually agency-licensed). Each item carries what the game needs to pick its own picture:

1. **`series` and `circuit`**: the game shows its own series badge and the circuit outline from its track data. Credit OpenStreetMap ("© OpenStreetMap contributors") wherever the outlines appear.
2. **`photo`**: set only when `data/photos.json` has an openly licensed photo for that circuit or series. Its credit is in the file's `photos` map and must be shown with the picture.

To add a photo, put the file in `data/photos/` and an entry in `data/photos.json`:

```json
{"id": "spa-eau-rouge", "file": "spa-eau-rouge.jpg", "title": "Eau Rouge",
 "author": "Jane Doe", "licence": "CC BY-SA 4.0",
 "licence_url": "https://creativecommons.org/licenses/by-sa/4.0/",
 "source_url": "https://commons.wikimedia.org/wiki/File:...",
 "circuit": "spa", "series": []}
```

Keep photos small (about 640 px wide) so the game downloads them quickly.

## Output format

`news.json`:

```json
{
  "schema_version": 1,
  "updated": "2026-11-13T14:17:00Z",
  "items": [
    {"title": "...", "source": "Sportscar365", "time": "2026-11-13T12:41:00Z",
     "url": "https://...", "series": ["imsa"], "circuit": "sebring", "photo": null,
     "also": [{"source": "Dailysportscar", "url": "https://...", "time": "2026-11-13T13:05:00Z"}]}
  ],
  "photos": {}
}
```

`calendar.json` has the same envelope with `events`: series and series name, year, round, name, format, circuit id and name, country, layout, start and end dates, status, sessions (UTC) and photo. For `tbc` events, round, circuit fields and dates can be `null`.

### 2027 calendar coverage

Typed from announcements found by web search in November 2026 (the official pages could not be opened from the research session); each file's `source_url` names the main source and `notes` flag disputed dates.

| File | Events | Status |
| --- | --- | --- |
| `f1` | 24 | Official; Turkey and Las Vegas provisional |
| `wec` | 9 | Official |
| `imsa` | 11 | Official; Road America date disputed |
| `btcc` | 10 events (30 races) | Official; Oulton Park and Silverstone provisional |
| `british-gt` | 6 events (8 races) | Provisional |
| `gtwc-europe` | 9 + 1 TBC | Provisional |
| `gtwc-america` | 7 | Provisional |
| `gtwc-asia` | 6 | Provisional |
| `gtwc-australia` | 5 + 1 TBC | Provisional |
| `igtc` | 2 (Bathurst 12h, Suzuka 1000km) | Other three rounds listed in their main series |
| `nls` | 8 + 24h Qualifiers + Nürburgring 24h | Provisional; the 24h is confirmed |
| `24h-series` | 6 | Kyalami confirmed, rest provisional |
| `super-taikyu` | 7 (incl. Fuji 24 Hours) | Provisional |
| `belcar` | 24 Hours of Zolder, TBC | Not announced |
| `dtm` | 8 | Official |
| `gt-masters` | 6 | Official (subject to change) |
| `super-gt` | 8 | Provisional; Suzuka 1000km is in `igtc` |
| `elms` | 6 | Official; Portimão moved to 16 Oct |
| `asian-lms` | 2 (Jerez, Portimão; 2026-27 season in Europe) | Official |
| `gt-world-cup` | Macau, TBC | 2027 not announced |
| `gt4` | 5 + 1 TBC | Provisional (GT4 European Series) |
| `gt-open` | 8 | Provisional |
| `porsche-supercup` | 8 | Provisional |
| `carrera-cup` | 8 events (16 races) | Provisional (Carrera Cup GB) |
| `super-trofeo` | 6 + World Final | Official (Super Trofeo Europe) |
| `ferrari-challenge` | 6 + Finali Mondiali TBC | Official (Ferrari Challenge Europe) |
| `pilot-challenge` | 10 | Official |

Where a source gave only race days (ELMS, GT Open, Supercup), the weekend is the usual Friday to race day and the event's `notes` say so.

IndyCar is not tracked, in the calendar or the news.

### 2026 calendar coverage

Only the rounds still to run when added (14 Nov 2026), with session times, from the [sportstimes](https://github.com/sportstimes/f1) calendar data (MIT licence, © Andrew Yates, Si Jobling, Andy Higgs):

| File | Events |
| --- | --- |
| `f1` | Las Vegas, Qatar, Abu Dhabi (rounds 21–23) |
| `motogp` | Portugal, Valencia (rounds 21–22). MotoGP is calendar-only: no headlines. |

News holds every matching headline from the last 14 days, newest first, with no per-series cap (a 1000-item ceiling exists only as a safety limit). Headlines stay for the full 14 days even after they drop out of the outlet's own feed. Series are matched on the headline and on the article's category tags.

The same story from several outlets is shown once: the outlet that published it first leads, and the others are listed in `also` (source, link, time), so the game can show "also on Dailysportscar". Two headlines count as the same story when they come from different outlets within 72 hours and share at least three meaningful words making up at least 30% of their combined words, or are identical. An outlet's own follow-up articles are never merged.

Past calendar events are kept; the game hides them.

`schema_version` changes only for breaking changes. The game should ignore a file whose version is newer than it understands.

## In the game

- Download `news.json` and `calendar.json` when Home opens, at most once an hour, and cache them. Ship a copy inside the build for first launch and offline play.
- Show "updated X ago" from `updated`.
- On tap, open the article with `OS.shell_open(url)`: the phone's browser on Android, a new tab on the web build.
- When `also` is not empty, show the other outlets under the headline (e.g. "also on Dailysportscar, Sportscar365"), each opening its own link.

## When things break

- A feed that fails keeps its last good headlines. After 7 days of failures the Action opens a GitHub issue labelled `stale-feed`.
- A bad data file fails the run and nothing is published; the previous files stay live.
- GitHub may start the hourly run late. Headlines can be up to about 90 minutes old.

## Setup (once)

1. Settings → Pages → Build and deployment → Source: **GitHub Actions**.
2. Merge to `main`. The workflow runs hourly from then on, or on demand from the Actions tab ("Run workflow").

## Running locally

```sh
python3 -m unittest discover -s tests   # tests
python3 scripts/build.py --offline      # validate data, rebuild from the cache, no network
python3 scripts/build.py                # fetch enabled feeds
```
