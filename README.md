# People · digiKam collection explorer

A small, private app for exploring a digiKam collection through its people tags. Python exports the relevant SQLite data; a dependency-free static web app does the rest.

## Quick start


```bash
python3 extract.py "/path/to/digikam4.db"
```

Then open **`site/index.html`** in your browser. It works directly from disk: the exporter writes a classic JavaScript data file, so there are no `file://` fetch/CORS restrictions.

Alternatively, serve it locally:

```bash
python3 -m http.server 8000 --bind 127.0.0.1 --directory site
# Open http://127.0.0.1:8000
```

The exporter opens the database with SQLite `mode=ro`, enables `query_only`, and uses one read transaction. It never changes your digiKam database. For a consistent snapshot of a busy collection, close digiKam first or export from a database backup. No image files are needed.

## What you can explore

- **All people tags**, including unused tags with zero photos; search and sort by count, name, or most recent photo date.
- **Click a person** for their photo-count timeline and co-tagged people.
- **Check multiple people** to see photos containing **every** selected person. “Select visible” adds all search results. Remove a group member with its chip’s × button.
- **Monthly or yearly timelines**, with an optional year filter. Counts and percentages are drawn in separate, clearly labeled panels. Hover/tap the chart, use arrow keys when it is focused, or switch to the accessible table.
- **Two percentage definitions:** share of tagged photos, or share of person-tag occurrences.
- **Companion rankings:** other people most often tagged in the same photos as the person or complete group. Click **+** to add a companion to the group.
- **CSV export** of the current timeline, including its exact denominators.
- A responsive layout, keyboard-accessible controls, and a URL hash that preserves the current selection and chart settings.

## Counting rules

| Metric | Definition |
|---|---|
| Person count | Distinct photos carrying that person’s tag. Multiple face regions do not multiply the count. |
| Group count | Distinct photos carrying **all** selected people’s tags (intersection, not union). |
| Tagged-photo share | Matching photos in a period ÷ photos with at least one included person tag in that period. |
| Person-tag share | Selected people’s tag occurrences in matching photos ÷ all included person-tag occurrences in that period. For a group of N people, each matching photo contributes N occurrences. |
| Companion count | Photos containing the person or complete group **and** the companion. Selected people are excluded from the ranking. |
| Companion percentage | Companion count ÷ all matching photos, including undated matching photos. |

A period with a zero denominator has **no percentage** (`—`), not a misleading 0%. A selected person with no appearances in an otherwise active period has 0%.

The three headline statistics and companion rankings cover **all dates**. The year selector filters only the timeline. The sidebar counts are always all-time counts.

### Dates

The database schema has photo dates, but **no timestamp for when a tag was assigned**. Timelines therefore describe when the photos were taken, not when you added the tags.

Default `--date-source auto` uses:

1. `ImageInformation.creationDate`;
2. `Albums.date` if the capture date is missing or invalid;
3. an unknown-date bucket if neither is usable.

The recorded calendar date is used without timezone conversion. Undated photos still contribute to total counts and companion rankings, but are excluded from timelines and their percentage denominators. The export reports how many photos used each date source. Empty months/years inside the date range are retained as zero-activity periods.

### Which tags are people?

Automatic discovery uses a top-level **People** tag and includes its descendants, plus tags carrying digiKam’s `person` property elsewhere. The People container itself is not counted as a person. An explicitly selected root is a strict subtree boundary.

Unknown/Unconfirmed person tags are excluded by default. General tags such as Landscape are not treated as people. Nested category tags under People are included by default; use `--person-property-only` if your hierarchy contains categories and you only want tags explicitly marked as people.

All rows in `Images` are included, regardless of visibility/status flags. Face regions, thumbnails, filenames, and album paths are not exported. Tag paths are retained to distinguish people with the same display name.

## Choosing a different People root

Inspect the hierarchy:

```bash
python3 extract.py "/path/to/digikam4.db" --list-roots
```

Then select a numeric tag ID (recommended) or a full slash-separated path:

```bash
python3 extract.py "/path/to/digikam4.db" --people-root 42
python3 extract.py "/path/to/digikam4.db" --people-root "Family/People"
```

Other options:

```bash
python3 extract.py "/path/to/digikam4.db" --person-property-only
python3 extract.py "/path/to/digikam4.db" --include-unknown
python3 extract.py "/path/to/digikam4.db" --date-source creation
python3 extract.py "/path/to/digikam4.db" --date-source album
python3 extract.py "/path/to/digikam4.db" --output site/data.js
python3 extract.py --help
```

`--list-roots` prints IDs, paths, descendant counts, and person-property counts without writing an export. If no People root or person-marked tags are found, the exporter explains how to choose a root rather than silently exporting unrelated tags.

## Privacy

The app makes **no network requests** and uses no external fonts, scripts, analytics, or CDNs. `site/data.js` contains people’s names, tag paths, dates aggregated by month, and anonymous photo-to-person associations. It does not contain image files, filenames, album paths, or the source database path.

Nevertheless, names and photo relationships can be sensitive. Keep the exported `site/data.js` private, and do not publish the `site` directory unless you intend to share that information. The optional server command above binds only to your own computer.

## Sample data

The sample is generated from the supplied schema with synthetic names and photo associations. A permanent banner and the export metadata identify it as synthetic. Running the extractor on your database replaces it.

To regenerate the sample:

```bash
python3 make_demo.py
```

## Tests

No test dependencies beyond Python and Node.js:

```bash
python3 -m unittest discover -s tests -v
node --test tests/analytics.test.js
```

The Python tests create temporary databases using `schema.sql` and cover tag discovery, custom roots, date fallback, unknown tags, empty collections, safe script output, and read-only behavior. The Node tests independently check intersections, both percentage denominators, zero periods, undated photos, and companion rankings.

## Files

```text
digikam-people/
├── extract.py             # Read-only SQLite → static data exporter
├── make_demo.py           # Reproducible synthetic sample generator
├── schema.sql             # The schema supplied with the request
├── README.md
├── site/
│   ├── index.html         # Open this file
│   ├── styles.css
│   ├── analytics.js       # Pure counting/intersection functions
│   ├── app.js             # UI, SVG charts, selection, CSV export
│   └── data.js            # Generated export; initially synthetic demo data
└── tests/
    ├── test_extract.py
    └── analytics.test.js
```

## Troubleshooting

- **“No people tags found”**: run `--list-roots`, then pass the correct `--people-root` ID. If your people tags use the `person` property, try `--person-property-only` with that root.
- **SQLite cannot open the database**: check the path and permissions. Close digiKam, or use a backup. A WAL-mode backup must include any accompanying `-wal` contents; making the backup through SQLite is safest.
- **Missing timeline dates**: inspect “Behind the numbers → Data quality & this export.” Use the default album-date fallback if capture dates are absent.
- **A group has no photos**: group mode requires everyone in the same photo. Remove a member or clear the selection to explore individuals.
- **Changes in digiKam are not visible**: run the exporter again and reload the browser. This is a static snapshot, not a live database connection.
- **Very large collections**: the browser loads the compact export into memory. There is no backend query service; extremely large collections may need a larger machine or a future aggregated/paginated export.
