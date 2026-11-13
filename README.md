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
| `data/series.json` | Series ids, names, and headline keywords used to tag multi-series feeds | You |
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
- `status` is `confirmed`, `provisional` or `cancelled`.
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

All feeds in the list start **disabled and unverified**: their addresses come from research that could not reach the sites. Check each one opens as RSS before enabling it.

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
     "url": "https://...", "series": ["imsa"], "circuit": "sebring", "photo": null}
  ],
  "photos": {}
}
```

`calendar.json` has the same envelope with `events`: series and series name, year, round, name, circuit id and name, country, layout, start and end dates, status, sessions (UTC) and photo.

Up to 40 headlines, at most 10 per source, none older than 14 days, newest first, duplicates removed. Past calendar events are kept; the game hides them.

`schema_version` changes only for breaking changes. The game should ignore a file whose version is newer than it understands.

## In the game

- Download `news.json` and `calendar.json` when Home opens, at most once an hour, and cache them. Ship a copy inside the build for first launch and offline play.
- Show "updated X ago" from `updated`.
- On tap, open the article with `OS.shell_open(url)`: the phone's browser on Android, a new tab on the web build.

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
