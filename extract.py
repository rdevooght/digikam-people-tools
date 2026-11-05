#!/usr/bin/env python3
"""Export digiKam people-tag statistics to a private, static JavaScript data file.

Python 3.10+; standard library only. The source database is opened read-only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
INTERNAL_ROOTS = {"_digikam_root_tag_", "_digikam_internal_tags_"}
UNKNOWN_NAMES = {"unknown", "unknown person", "unknown people", "unconfirmed"}


class ExtractionError(Exception):
    """An actionable input/schema error (no traceback needed on the CLI)."""


def connect_readonly(database: Path) -> sqlite3.Connection:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise ExtractionError(f"Database not found: {database}")
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")  # One consistent snapshot for the whole export.
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise ExtractionError(
            f"Cannot open the database read-only: {exc}. Close digiKam or work "
            "on a copy of digikam4.db (include its -wal file if present)."
        ) from exc


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    # Table names here come from our fixed allowlist, never from user input.
    allowed = {"Tags", "ImageTags", "TagProperties", "Images", "ImageInformation", "Albums"}
    if table not in allowed:
        raise ValueError(table)
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def parse_photo_date(value: Any) -> str | None:
    """Return the recorded calendar date, without imposing a timezone conversion."""
    if value is None or isinstance(value, (int, float)):
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def month_range(first: str, last: str) -> list[str]:
    year, month = map(int, first.split("-"))
    end_year, end_month = map(int, last.split("-"))
    result = []
    while (year, month) <= (end_year, end_month):
        result.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return result


def tag_paths(tags: dict[int, dict], root_id: int, internal: set[int] | None = None) -> list[int]:
    """Bottom-up path; omit digiKam's virtual root and detect broken cycles."""
    internal = internal_root_ids(tags) if internal is None else internal
    chain = []
    seen = set()
    current: int | None = root_id
    while current in tags and current not in internal:
        if current in seen:
            raise ExtractionError(f"Cycle in Tags.pid involving tag {current}.")
        seen.add(current)
        chain.append(current)
        parent = tags[current]["pid"]
        if parent in (None, 0, -1):
            break
        current = int(parent)
    return list(reversed(chain))


def internal_root_ids(tags: dict[int, dict]) -> set[int]:
    return {tid for tid, tag in tags.items() if tag["name"].casefold() in INTERNAL_ROOTS}


def descendants(tags: dict[int, dict], roots: set[int], include_roots: bool = True) -> set[int]:
    children: dict[int, list[int]] = defaultdict(list)
    for tid, tag in tags.items():
        parent = tag["pid"]
        if parent is not None:
            children[int(parent)].append(tid)
    found = set(roots) if include_roots else set()
    pending = list(roots)
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in found:
                found.add(child)
                pending.append(child)
    return found


def resolve_root(tags: dict[int, dict], paths: dict[int, str], selector: str) -> int:
    if re.fullmatch(r"-?\d+", selector.strip()):
        tid = int(selector)
        if tid not in tags:
            raise ExtractionError(f"No tag with ID {tid}. Use --list-roots to inspect the hierarchy.")
        return tid
    wanted = selector.strip().strip("/").casefold()
    if not wanted:
        raise ExtractionError("--people-root cannot be empty.")
    matches = [tid for tid, path in paths.items() if path.casefold() == wanted]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise ExtractionError(f"No tag path matches {selector!r}. Try --list-roots or a numeric tag ID.")
    choices = ", ".join(f"{tid}: {paths[tid]}" for tid in matches)
    raise ExtractionError(f"Ambiguous tag path. Select an ID instead. Matches: {choices}")


def discover_people(
    tags: dict[int, dict],
    properties: dict[int, set[str]],
    paths: dict[int, str],
    selector: str | None,
    person_property_only: bool,
    include_unknown: bool,
) -> tuple[set[int], list[int], list[str]]:
    warnings: list[str] = []
    internal = internal_root_ids(tags)
    marked = {tid for tid, props in properties.items() if "person" in props and tid in tags}
    unknown_roots = {
        tid for tid, tag in tags.items()
        if "unknownperson" in properties.get(tid, set())
        or tag["name"].strip().casefold() in UNKNOWN_NAMES
    }
    unknown = descendants(tags, unknown_roots) - internal
    virtual_roots = internal | {0, -1}
    top_level = {
        tid for tid, tag in tags.items()
        if tid not in internal and (tag["pid"] in (None, 0, -1) or tag["pid"] not in tags or tag["pid"] in virtual_roots)
    }
    if selector is not None:
        root_ids = [resolve_root(tags, paths, selector)]
        scope = descendants(tags, set(root_ids), include_roots=False)
    else:
        named_roots = {tid for tid in top_level if tags[tid]["name"].strip().casefold() == "people"}
        property_roots = {
            tid for tid, props in properties.items()
            if any(p.startswith("people") and p.endswith("root") for p in props)
        }
        named_roots |= property_roots - internal
        if len(named_roots) == 1:
            root_ids = list(named_roots)
            scope = descendants(tags, named_roots, include_roots=False)
        elif len(named_roots) > 1:
            choices = ", ".join(f"{tid}: {paths[tid]}" for tid in sorted(named_roots))
            raise ExtractionError(f"Multiple People roots found; choose --people-root ID. Candidates: {choices}")
        elif marked:
            root_ids = []
            scope = set(marked)
            warnings.append("No People root found; using tags with the digiKam 'person' property.")
        else:
            raise ExtractionError(
                "No people tags found. Run --list-roots, then specify --people-root ID_OR_PATH. "
                "General photo tags are never silently treated as people."
            )
    if selector is None and root_ids:
        # With automatic root discovery, also honor person tags outside that tree.
        # An explicitly selected root is a strict boundary instead.
        scope |= marked - set(root_ids)
    if person_property_only:
        scope &= marked
    scope -= internal | set(root_ids)
    if not include_unknown:
        scope -= unknown
    if not scope:
        raise ExtractionError("The selected people scope contains no person tags. Check --people-root and the filter options.")
    return scope, root_ids, warnings


def read_date_sources(connection: sqlite3.Connection) -> tuple[dict[int, tuple[Any, Any]], list[str]]:
    images = table_columns(connection, "Images")
    if "id" not in images:
        return {}, ["Images.id is missing; photo dates will be unavailable."]
    info = table_columns(connection, "ImageInformation")
    albums = table_columns(connection, "Albums")
    creation = "ii.creationDate" if {"imageid", "creationDate"} <= info else "NULL"
    album_date = "a.date" if {"id", "date"} <= albums else "NULL"
    join_info = 'LEFT JOIN ImageInformation ii ON ii.imageid = i.id' if creation != "NULL" else ""
    join_album = 'LEFT JOIN Albums a ON a.id = i.album' if album_date != "NULL" and "album" in images else ""
    rows = connection.execute(f"SELECT i.id, {creation} AS creation, {album_date} AS album_date FROM Images i {join_info} {join_album}")
    return {int(row["id"]): (row["creation"], row["album_date"]) for row in rows}, []


def extract_database(
    database: Path,
    *,
    people_root: str | None = None,
    date_source: str = "auto",
    include_unknown: bool = False,
    person_property_only: bool = False,
    demo: bool = False,
) -> dict[str, Any]:
    if date_source not in {"auto", "creation", "album"}:
        raise ExtractionError("date_source must be auto, creation, or album.")
    connection = connect_readonly(database)
    try:
        for table, required in {"Tags": {"id", "pid", "name"}, "ImageTags": {"imageid", "tagid"}}.items():
            missing = required - table_columns(connection, table)
            if missing:
                raise ExtractionError(f"{table} is missing required columns: {', '.join(sorted(missing))}. Supply a digiKam4.db database, not a schema-only file.")
        tags = {
            int(row["id"]): {"pid": int(row["pid"]) if row["pid"] is not None else None, "name": str(row["name"])}
            for row in connection.execute("SELECT id, pid, name FROM Tags")
        }
        paths = {tid: "/".join(tags[parent]["name"] for parent in tag_paths(tags, tid)) for tid in tags}
        properties: dict[int, set[str]] = defaultdict(set)
        if {"tagid", "property"} <= table_columns(connection, "TagProperties"):
            for row in connection.execute("SELECT tagid, property FROM TagProperties WHERE tagid IS NOT NULL AND property IS NOT NULL"):
                properties[int(row["tagid"])].add(str(row["property"]).strip().casefold())
        person_ids, root_ids, warnings = discover_people(
            tags, properties, paths, people_root, person_property_only, include_unknown
        )
        image_dates, date_warnings = read_date_sources(connection)
        warnings.extend(date_warnings)
        assignments: dict[int, set[int]] = defaultdict(set)
        unknown_tagged: set[int] = set()
        unknown_ids = {
            tid for tid, tag in tags.items()
            if "unknownperson" in properties.get(tid, set()) or tag["name"].strip().casefold() in UNKNOWN_NAMES
        }
        for row in connection.execute("SELECT DISTINCT imageid, tagid FROM ImageTags"):
            image_id, tag_id = int(row["imageid"]), int(row["tagid"])
            if tag_id in person_ids:
                assignments[image_id].add(tag_id)
            elif not include_unknown and tag_id in unknown_ids:
                unknown_tagged.add(image_id)
        total_photos = connection.execute("SELECT COUNT(*) FROM Images").fetchone()[0] if "id" in table_columns(connection, "Images") else len(assignments)
    finally:
        connection.close()

    chosen_dates: dict[int, str | None] = {}
    sources: Counter[str] = Counter()
    invalid_creation = 0
    for image_id in assignments:
        creation_raw, album_raw = image_dates.get(image_id, (None, None))
        creation_date = parse_photo_date(creation_raw)
        album_date = parse_photo_date(album_raw)
        if creation_raw and not creation_date:
            invalid_creation += 1
        chosen: str | None = None
        source = "undated"
        if date_source in {"auto", "creation"} and creation_date:
            chosen, source = creation_date, "creation"
        elif date_source in {"auto", "album"} and album_date:
            chosen, source = album_date, "album"
        chosen_dates[image_id] = chosen
        sources[source] += 1
    dated = sorted(d for d in chosen_dates.values() if d is not None)
    months = month_range(dated[0][:7], dated[-1][:7]) if dated else []
    month_index = {month: index for index, month in enumerate(months)}
    photos = [
        [month_index[chosen_dates[image_id][:7]] if chosen_dates[image_id] else -1, sorted(person_ids)]
        for image_id, person_ids in assignments.items()
    ]
    photos.sort(key=lambda photo: (photo[0] < 0, photo[0]))
    counts: Counter[int] = Counter()
    first: dict[int, str] = {}
    last: dict[int, str] = {}
    undated: Counter[int] = Counter()
    for month, people in photos:
        for tid in people:
            counts[tid] += 1
            if month < 0:
                undated[tid] += 1
            else:
                first[tid] = min(first.get(tid, months[month]), months[month])
                last[tid] = max(last.get(tid, months[month]), months[month])
    people = [
        {
            "id": tid, "name": tags[tid]["name"], "path": paths[tid],
            "count": counts[tid], "first": first.get(tid), "last": last.get(tid),
            "undated": undated[tid],
        }
        for tid in sorted(person_ids, key=lambda tid: (-counts[tid], tags[tid]["name"].casefold(), tid))
    ]
    orphan_count = sum(image_id not in image_dates for image_id in assignments)
    if orphan_count:
        warnings.append(f"{orphan_count} tagged photo IDs have no Images row; retained with an unknown date.")
    if invalid_creation:
        warnings.append(f"{invalid_creation} tagged photos have an invalid creation date; the selected date policy was applied.")
    if sources["undated"]:
        warnings.append(f"{sources['undated']} tagged photos have no usable date; included in totals and co-tags, excluded from timelines.")
    if not include_unknown and unknown_tagged:
        warnings.append(f"Unknown-person tags on {len(unknown_tagged)} photos are excluded. Use --include-unknown to include them.")
    return {
        "version": SCHEMA_VERSION,
        "meta": {
            "demo": demo,
            "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "peopleRoot": paths[root_ids[0]] if root_ids else "Tags marked person",
            "rootIds": root_ids,
            "dateSource": date_source,
            "includeUnknown": include_unknown,
            "personPropertyOnly": person_property_only,
            "totalPhotos": total_photos,
            "taggedPhotos": len(assignments),
            "tagAssignments": sum(counts.values()),
            "peopleCount": len(people),
            "undatedPhotos": sources["undated"],
            "creationDatePhotos": sources["creation"],
            "albumDatePhotos": sources["album"],
            "unknownTaggedPhotos": len(unknown_tagged) if not include_unknown else 0,
            "orphanTaggedPhotos": orphan_count,
            "warnings": warnings,
        },
        "months": months,
        "people": people,
        "photos": photos,  # [month index (-1 = unknown), sorted person tag IDs]; no filenames or paths.
    }


def list_roots(database: Path) -> None:
    connection = connect_readonly(database)
    try:
        if not {"id", "pid", "name"} <= table_columns(connection, "Tags"):
            raise ExtractionError("This database does not have the expected Tags table.")
        tags = {int(r["id"]): {"pid": r["pid"], "name": str(r["name"])} for r in connection.execute("SELECT id, pid, name FROM Tags")}
        paths = {tid: "/".join(tags[p]["name"] for p in tag_paths(tags, tid)) for tid in tags}
        internal = internal_root_ids(tags)
        roots = [tid for tid, tag in tags.items() if tid not in internal and (tag["pid"] in (None, 0, -1) or tag["pid"] not in tags or tag["pid"] in internal)]
        marked: set[int] = set()
        if {"tagid", "property"} <= table_columns(connection, "TagProperties"):
            marked = {int(r[0]) for r in connection.execute("SELECT tagid FROM TagProperties WHERE lower(property) = 'person'")}
        print(f"{'ID':>7}  {'PERSON-MARKED':>13}  {'DESCENDANTS':>11}  TAG PATH")
        for tid in sorted(roots, key=lambda tid: (tags[tid]["name"].casefold(), tid)):
            subtree = descendants(tags, {tid}, include_roots=False)
            print(f"{tid:>7}  {len(subtree & marked):>13}  {len(subtree):>11}  {paths[tid]}")
        if marked and not roots:
            print("Person-marked tags:")
            for tid in sorted(marked):
                print(f"  {tid}: {paths.get(tid, tid)}")
    finally:
        connection.close()


def write_data(data: dict[str, Any], output: Path) -> None:
    output = output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    # Safe as a classic script, including when index.html is opened with file://.
    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    payload = payload.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    content = f"// digiKam people export. Generated by extract.py; no image files or filenames included.\nwindow.DIGIKAM_DATA = {payload};\n"
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=output.parent, prefix=".people-", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
        os.replace(temporary, output)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("database", type=Path, help="Path to digikam4.db (opened read-only)")
    parser.add_argument("-o", "--output", type=Path, default=Path(__file__).resolve().parent / "site" / "data.js")
    parser.add_argument("--people-root", help="People tag ID or full slash-separated path; auto-detected by default")
    parser.add_argument("--list-roots", action="store_true", help="Print top-level tags and exit without exporting")
    parser.add_argument("--date-source", choices=["auto", "creation", "album"], default="auto", help="auto = creation date, then album date (default)")
    parser.add_argument("--include-unknown", action="store_true", help="Include Unknown/Unconfirmed person tags")
    parser.add_argument("--person-property-only", action="store_true", help="Only include tags carrying digiKam's person property")
    args = parser.parse_args(argv)
    try:
        if args.list_roots:
            list_roots(args.database)
            return 0
        if args.output.expanduser().resolve() == args.database.expanduser().resolve():
            raise ExtractionError("The output path must not be the source database.")
        data = extract_database(
            args.database, people_root=args.people_root, date_source=args.date_source,
            include_unknown=args.include_unknown, person_property_only=args.person_property_only,
        )
        write_data(data, args.output)
        meta = data["meta"]
        for warning in meta["warnings"]:
            print(f"Note: {warning}", file=sys.stderr)
        print(f"Exported {meta['peopleCount']} people, {meta['taggedPhotos']:,} tagged photos, {meta['tagAssignments']:,} person tags.")
        print(f"Open {args.output.resolve().parent / 'index.html'} in your browser.")
        return 0
    except (ExtractionError, sqlite3.Error, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
