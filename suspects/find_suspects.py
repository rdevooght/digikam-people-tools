#!/usr/bin/env python3
"""Find confirmed digiKam face tags that are probably wrong.

digiKam's face recognition learns from every face you confirm, so one wrong
confirmation can spread. This tool reads digiKam's two SQLite databases and
the face embeddings stored in recognition.db, scores every confirmed face, and
writes an HTML report plus a CSV that show *which photo and which face* to
check. You then correct it in digiKam; nothing is ever modified here.

Python 3.10+; standard library only. Both databases are opened read-only and
image files are only referenced by the report, never opened or changed.

Settings come from the command line, then the environment, then a .env file:
    DIGIKAM_DB          path to digikam4.db
    DIGIKAM_FACES_DB    path to recognition.db (default: next to digikam4.db)
    DIGIKAM_COLLECTION  folder of the image collection (the album root)
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import heapq
import json
import math
import operator
import os
import re
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

# --- Names of the settings (change here if your .env uses other names) -----
ENV_DB = "DIGIKAM_DB"
ENV_FACES_DB = "DIGIKAM_FACES_DB"
ENV_COLLECTION = "DIGIKAM_COLLECTION"

TEMPLATE = Path(__file__).with_name("report_template.html")
SCHEMA_VERSION = 1

# --- Scoring: every number that influences the ranking is here -------------
MATCH_THRESHOLD = 0.363   # cosine similarity above which two faces count as "the same person"
SUPPORT_K = 3             # a face's support = mean of its K most similar reference faces
MIN_OWN_REFERENCES = 2    # fewer same-person references than this: no embedding verdict
POINTS = {
    "closer_to_other_base": 2.0,   # closer to another person than to its own person ...
    "closer_to_other_extra": 4.0,  # ... plus up to this much, growing with the margin
    "margin_full": 0.15,           # similarity margin at which the extra points are maxed out
    "unsupported_base": 1.0,       # no good match among the person's other faces ...
    "unsupported_extra": 2.0,      # ... plus up to this much, growing with the shortfall
    "unsupported_full": 0.20,      # shortfall at which the extra points are maxed out
    "duplicate": 2.0,              # same person tagged twice in one photo
    "duplicate_weakest": 2.0,      # extra for the copy that matches its person least
    "overlap": 3.0,                # same face area tagged as two different people
    "mismatch": 2.0,               # training data and tag name two different people
    "context": 0.5,                # each weak context hint (never counts alone)
}
FACE_PAD = 1.7            # report crops show the box enlarged by this factor


class SuspectError(Exception):
    """An actionable input/schema error (no traceback needed on the CLI)."""


# ===========================================================================
# Settings (.env)
# ===========================================================================
def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=VALUE, optional `export`, quotes, # comments."""
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise SuspectError(f"{path}:{number}: expected KEY=VALUE, got {line!r}")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    return values


def find_env_file(explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise SuspectError(f"Env file not found: {explicit}")
        return explicit
    for candidate in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"):
        if candidate.is_file():
            return candidate
    return None


@dataclass
class Settings:
    db: Path
    faces_db: Path
    collection: Path | None


def resolve_settings(args: argparse.Namespace, environ: dict[str, str] | None = None) -> Settings:
    """Precedence: command line, then process environment, then the .env file."""
    environ = os.environ if environ is None else environ
    env_file = find_env_file(args.env_file)
    from_file = parse_env_file(env_file) if env_file else {}
    base = env_file.parent if env_file else Path.cwd()

    def pick(cli: Path | None, name: str) -> Path | None:
        if cli is not None:
            return cli.expanduser()
        raw = environ.get(name) or from_file.get(name)
        if not raw:
            return None
        path = Path(raw).expanduser()
        # Relative paths written in a .env file are relative to that file.
        return path if path.is_absolute() or name in environ else base / path

    db = pick(args.db, ENV_DB)
    if db is None:
        raise SuspectError(
            f"No digiKam database configured. Set {ENV_DB} in a .env file "
            f"(see .env.example) or pass --db /path/to/digikam4.db."
        )
    faces_db = pick(args.faces_db, ENV_FACES_DB) or db.with_name("recognition.db")
    return Settings(db=db, faces_db=faces_db, collection=pick(args.collection, ENV_COLLECTION))


# ===========================================================================
# Database access (read-only)
# ===========================================================================
def connect_readonly(database: Path) -> sqlite3.Connection:
    database = database.expanduser().resolve()
    if not database.is_file():
        raise SuspectError(f"Database not found: {database}")
    connection = None
    try:
        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.execute("BEGIN")  # one consistent snapshot
        return connection
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise SuspectError(
            f"Cannot open {database.name} read-only: {exc}. Close digiKam or work "
            "on a copy (include the -wal file if present)."
        ) from exc


def table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    allowed = {"FaceMatrices", "Identities", "IdentityAttributes", "Settings"}
    if table not in allowed:  # table names come from this fixed list only
        raise ValueError(table)
    return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}


def chunks(items: Sequence[Any], size: int = 500) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


# ===========================================================================
# The face hash that links an embedding to its photo
# ===========================================================================
def nibble_string(number: int) -> str:
    """digiKam's fastNumberToString: 16 letters, one per 4-bit nibble, low first."""
    number &= (1 << 64) - 1
    return "".join(chr(ord("a") + ((number >> (4 * i)) & 0xF)) for i in range(16))


def region_xml(x: int, y: int, width: int, height: int) -> str:
    return f'<rect x="{x}" y="{y}" width="{width}" height="{height}"/>'


def face_hash(image_id: int, tag_id: int, xml: str) -> str:
    """FaceMatrices.removeHash = SHA1(nib(imageId) + nib(tagId) + region XML)."""
    data = nibble_string(image_id) + nibble_string(tag_id) + xml
    return hashlib.sha1(data.encode("latin-1", "replace")).hexdigest()


_ATTR = re.compile(r'(\w+)\s*=\s*"(-?\d+)"')


def parse_region(text: str | None) -> tuple[int, int, int, int] | None:
    if not text:
        return None
    attrs = {k: int(v) for k, v in _ATTR.findall(text)}
    try:
        box = (attrs["x"], attrs["y"], attrs["width"], attrs["height"])
    except KeyError:
        return None
    return box if box[2] > 0 and box[3] > 0 else None


# ===========================================================================
# Data model
# ===========================================================================
@dataclass
class Face:
    """A confirmed face tag from digikam4.db (ImageTagProperties 'tagRegion')."""
    image_id: int
    tag_id: int
    box: tuple[int, int, int, int]
    raw: str
    sample: int | None = None   # index of its embedding in `samples`, if trained


@dataclass
class Sample:
    """One training row of recognition.db (FaceMatrices)."""
    row_id: int
    identity: int
    hash: str
    vec: tuple[float, ...]
    face: int | None = None     # index in `faces`; None = orphan (no live face)


@dataclass
class Photo:
    image_id: int
    name: str
    album_id: int | None
    album_path: str
    date: str | None
    day: int | None
    display_size: tuple[int, int] | None


@dataclass
class Analysis:
    faces: list[Face]
    samples: list[Sample]
    photos: dict[int, Photo]
    tag_names: dict[int, str]
    tag_paths: dict[int, str]
    identity_names: dict[int, str]
    person_tags: set[int]
    model_info: dict[str, str]
    tag_identity: dict[int, int | None]   # person tag -> identity learned for it (via faceEngineUuid)
    warnings: list[str] = field(default_factory=list)


# ===========================================================================
# Loading
# ===========================================================================
def similarity(a: Sequence[float], b: Sequence[float]) -> float:
    return _sumprod(a, b)


try:  # math.sumprod needs Python 3.12; the fallback is slower but identical.
    from math import sumprod as _sumprod
except ImportError:  # pragma: no cover
    def _sumprod(a: Sequence[float], b: Sequence[float]) -> float:
        return sum(map(operator.mul, a, b))


def unit(vec: Sequence[float]) -> tuple[float, ...]:
    norm = math.sqrt(_sumprod(vec, vec))
    return tuple(vec) if norm == 0 else tuple(v / norm for v in vec)


def load_samples(faces_db: sqlite3.Connection) -> tuple[list[Sample], dict[int, str], dict[str, str], list[str]]:
    warnings: list[str] = []
    try:
        columns = table_columns(faces_db, "FaceMatrices")
    except sqlite3.Error as exc:
        raise SuspectError(f"recognition.db is not usable: {exc}") from exc
    if not columns:
        raise SuspectError(
            "recognition.db has no FaceMatrices table. This tool needs digiKam 8.x "
            "with the neural-network (SFace) face recognition."
        )
    if "removeHash" not in columns:
        raise SuspectError(
            "FaceMatrices has no removeHash column, so embeddings cannot be linked "
            "to photos. Open the collection once in a recent digiKam 8.x to upgrade it."
        )
    samples: list[Sample] = []
    dims: Counter[int] = Counter()
    skipped = 0
    for row in faces_db.execute("SELECT id, identity, removeHash, embedding FROM FaceMatrices ORDER BY id"):
        blob = row["embedding"]
        if not blob or len(blob) % 4:
            skipped += 1
            continue
        count = len(blob) // 4
        dims[count] += 1
        samples.append(Sample(row["id"], row["identity"], row["removeHash"], struct.unpack(f"<{count}f", blob)))
    if dims:
        dominant = dims.most_common(1)[0][0]
        before = len(samples)
        samples = [s for s in samples if len(s.vec) == dominant]
        skipped += before - len(samples)
        if dominant != 128:
            warnings.append(f"Embeddings have {dominant} dimensions (SFace uses 128); results may be less reliable.")
    if skipped:
        warnings.append(f"{skipped} training rows had an unusable embedding and were ignored.")
    for sample in samples:
        sample.vec = unit(sample.vec)

    names: dict[int, str] = {}
    try:
        for row in faces_db.execute(
            "SELECT id, attribute, value FROM IdentityAttributes WHERE attribute IN ('name', 'fullName') ORDER BY attribute ASC"
        ):
            names[row["id"]] = row["value"]  # 'name' sorts after 'fullName', so it wins
    except sqlite3.Error:
        warnings.append("Could not read identity names from recognition.db.")
    model: dict[str, str] = {}
    try:
        for row in faces_db.execute("SELECT keyword, value FROM Settings WHERE keyword IN ('ExtractorModel', 'TrainingVersion')"):
            model[row["keyword"]] = row["value"]
    except sqlite3.Error:
        pass
    return samples, names, model, warnings


def load_identity_uuids(faces_db: sqlite3.Connection) -> dict[str, int]:
    try:
        return {r["value"]: r["id"] for r in faces_db.execute("SELECT id, value FROM IdentityAttributes WHERE attribute = 'uuid'")}
    except sqlite3.Error:
        return {}


def load_tags(core: sqlite3.Connection) -> tuple[dict[int, str], dict[int, str], set[int], dict[int, str]]:
    parents: dict[int, int] = {}
    names: dict[int, str] = {}
    for row in core.execute("SELECT id, pid, name FROM Tags"):
        parents[row["id"]], names[row["id"]] = row["pid"] or 0, row["name"]
    paths: dict[int, str] = {}
    for tag_id in names:
        parts, current, seen = [], tag_id, set()
        while current in names and current not in seen:
            seen.add(current)
            parts.append(names[current])
            current = parents.get(current, 0)
        paths[tag_id] = "/".join(reversed(parts))
    person_tags: set[int] = set()
    uuids: dict[int, str] = {}
    for row in core.execute("SELECT tagid, property, value FROM TagProperties WHERE property IN ('person', 'faceEngineUuid')"):
        person_tags.add(row["tagid"])
        if row["property"] == "faceEngineUuid" and row["value"]:
            uuids[row["tagid"]] = row["value"]
    return names, paths, person_tags, uuids


def load_faces(core: sqlite3.Connection) -> tuple[list[Face], int]:
    faces: list[Face] = []
    bad = 0
    for row in core.execute("SELECT imageid, tagid, value FROM ImageTagProperties WHERE property = 'tagRegion' ORDER BY imageid, tagid"):
        box = parse_region(row["value"])
        if box is None:
            bad += 1
            continue
        faces.append(Face(row["imageid"], row["tagid"], box, row["value"]))
    return faces, bad


def link_faces_to_samples(faces: list[Face], samples: list[Sample]) -> None:
    """Attach each trained face to its embedding through the face hash."""
    for sample in samples:
        sample.face = None
    for face in faces:
        face.sample = None
    by_hash = {s.hash: i for i, s in enumerate(samples)}
    for index, face in enumerate(faces):
        rebuilt = region_xml(*face.box)
        for xml in dict.fromkeys((face.raw, rebuilt)):
            hit = by_hash.get(face_hash(face.image_id, face.tag_id, xml))
            if hit is not None and samples[hit].face is None:
                samples[hit].face = index
                face.sample = hit
                break


def parse_day(*values: Any) -> tuple[str | None, int | None]:
    """First usable date among the arguments, as (ISO date, ordinal day)."""
    for value in values:
        if not value or isinstance(value, (int, float)):
            continue
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                parsed = date.fromisoformat(text[:10])
            except ValueError:
                continue
        return parsed.isoformat(), parsed.toordinal()
    return None, None


def load_photos(core: sqlite3.Connection, image_ids: Sequence[int]) -> dict[int, Photo]:
    photos: dict[int, Photo] = {}
    for part in chunks(sorted(set(image_ids))):
        marks = ",".join("?" * len(part))
        rows = core.execute(
            f"""SELECT Images.id AS id, Images.name AS name, Images.album AS album,
                       Albums.relativePath AS path, Albums.date AS album_date,
                       ImageInformation.creationDate AS created,
                       ImageInformation.width AS width, ImageInformation.height AS height,
                       ImageInformation.orientation AS orientation
                FROM Images
                LEFT JOIN Albums ON Albums.id = Images.album
                LEFT JOIN ImageInformation ON ImageInformation.imageid = Images.id
                WHERE Images.id IN ({marks})""",
            list(part),
        )
        for row in rows:
            iso, day = parse_day(row["created"], row["album_date"])
            size = None
            if row["width"] and row["height"] and row["width"] > 0 and row["height"] > 0:
                size = (row["width"], row["height"])
                if (row["orientation"] or 1) in (5, 6, 7, 8):  # EXIF: rotated a quarter turn
                    size = (size[1], size[0])
            photos[row["id"]] = Photo(row["id"], row["name"], row["album"], (row["path"] or "/").strip("/"), iso, day, size)
    return photos


def load_analysis(core: sqlite3.Connection, faces_db: sqlite3.Connection) -> Analysis:
    samples, identity_names, model_info, warnings = load_samples(faces_db)
    tag_names, tag_paths, person_tags, tag_uuids = load_tags(core)
    faces, bad_regions = load_faces(core)
    if bad_regions:
        warnings.append(f"{bad_regions} face regions in digikam4.db could not be read and were ignored.")
    link_faces_to_samples(faces, samples)
    # Only person tags count for duplicates and context. The tag of any trained
    # face is certainly a person, so add those to the tags digiKam marks as people.
    person_tags |= {faces[s.face].tag_id for s in samples if s.face is not None}
    faces = [f for f in faces if f.tag_id in person_tags]
    link_faces_to_samples(faces, samples)  # indexes changed with the filter
    photos = load_photos(core, [f.image_id for f in faces])
    uuid_to_identity = load_identity_uuids(faces_db)
    tag_identity = {tag: uuid_to_identity.get(uuid) for tag, uuid in tag_uuids.items()}
    return Analysis(faces, samples, photos, tag_names, tag_paths, identity_names,
                    person_tags, model_info, tag_identity, warnings)


# ===========================================================================
# Embedding evidence
# ===========================================================================
def top_mean(values: Iterable[float], k: int = SUPPORT_K) -> float | None:
    best = heapq.nlargest(k, values)
    return sum(best) / len(best) if best else None


@dataclass
class Evidence:
    own_support: float | None = None        # similarity to the person's other faces
    own_global: float | None = None
    own_era: float | None = None
    era_count: int = 0
    other_support: float | None = None      # similarity to the closest other person
    other_identity: int | None = None
    own_nearest: list[tuple[int, float]] = field(default_factory=list)    # (sample, similarity)
    other_nearest: list[tuple[int, float]] = field(default_factory=list)
    foreign_best: tuple[int, float] | None = None   # closest face of anybody else
    pulls: int = 0                                  # faces of others for which this is the closest foreign face


def reference_sets(samples: list[Sample], max_ref: int) -> dict[int, list[int]]:
    """Members of each identity; very large identities are sampled evenly."""
    members: dict[int, list[int]] = defaultdict(list)
    for index, sample in enumerate(samples):
        members[sample.identity].append(index)
    if max_ref > 0:
        for identity, items in members.items():
            if len(items) > max_ref:
                step = len(items) / max_ref
                members[identity] = [items[int(i * step)] for i in range(max_ref)]
    return members


def compute_evidence(
    analysis: Analysis, *, max_ref: int, candidates: int, era_days: int, threshold: float, progress: bool = False
) -> list[Evidence]:
    samples, faces, photos = analysis.samples, analysis.faces, analysis.photos
    refs = reference_sets(samples, max_ref)
    everyone: dict[int, list[int]] = defaultdict(list)
    for index, sample in enumerate(samples):
        everyone[sample.identity].append(index)
    centroids = {i: unit([sum(col) for col in zip(*(samples[j].vec for j in members))]) for i, members in everyone.items()}

    def day_of(index: int) -> int | None:
        face = samples[index].face
        photo = photos.get(faces[face].image_id) if face is not None else None
        return photo.day if photo else None

    days = [day_of(i) for i in range(len(samples))]
    evidence = [Evidence() for _ in samples]
    for index, sample in enumerate(samples):
        if progress and index and index % 2000 == 0:
            print(f"  compared {index:,} of {len(samples):,} faces...", file=sys.stderr)
        ev = evidence[index]
        own = [(j, similarity(sample.vec, samples[j].vec)) for j in refs[sample.identity] if j != index]
        if len(own) >= MIN_OWN_REFERENCES:
            ev.own_global = top_mean(s for _, s in own)
            ev.own_nearest = heapq.nlargest(2, own, key=lambda item: item[1])
            ev.own_support = ev.own_global
            if days[index] is not None:
                era = [s for j, s in own if days[j] is not None and abs(days[j] - days[index]) <= era_days]
                ev.era_count = len(era)
                if len(era) >= MIN_OWN_REFERENCES:
                    ev.own_era = top_mean(era)
                    # Benefit of the doubt: a face that matches the photos of its own
                    # period is fine even if the person looked different years ago.
                    ev.own_support = max(ev.own_global, ev.own_era)
        near = sorted(
            ((identity, similarity(sample.vec, c)) for identity, c in centroids.items() if identity != sample.identity),
            key=lambda item: item[1], reverse=True,
        )[:candidates]
        best_support = None
        for identity, _ in near:
            sims = [(j, similarity(sample.vec, samples[j].vec)) for j in refs[identity]]
            support = top_mean(s for _, s in sims)
            best_single = max(sims, key=lambda item: item[1])
            if ev.foreign_best is None or best_single[1] > ev.foreign_best[1]:
                ev.foreign_best = best_single
            if best_support is None or support > best_support:
                best_support = support
                ev.other_identity = identity
                ev.other_nearest = heapq.nlargest(2, sims, key=lambda item: item[1])
        ev.other_support = best_support
    for ev in evidence:
        if ev.foreign_best and ev.foreign_best[1] >= threshold:
            evidence[ev.foreign_best[0]].pulls += 1
    return evidence


# ===========================================================================
# Scoring
# ===========================================================================
@dataclass
class Reason:
    code: str
    points: float
    text: str


def iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    inter = max(0, right - left) * max(0, bottom - top)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union else 0.0


def face_person(analysis: Analysis, face: Face) -> str:
    return analysis.tag_names.get(face.tag_id, f"tag #{face.tag_id}")


def identity_name(analysis: Analysis, identity: int | None) -> str:
    if identity is None:
        return "someone else"
    return analysis.identity_names.get(identity, f"identity #{identity}")


@dataclass
class Suspect:
    face: int
    reasons: list[Reason]
    points: float
    pulls: int

    @property
    def priority(self) -> float:
        return self.points * (1 + math.log1p(self.pulls))


def score_faces(
    analysis: Analysis, evidence: list[Evidence], *, threshold: float, era_days: int, min_appearances: int
) -> list[Suspect]:
    faces, samples, photos = analysis.faces, analysis.samples, analysis.photos
    by_photo: dict[int, list[int]] = defaultdict(list)
    images_of_tag: dict[int, set[int]] = defaultdict(set)
    albums_of_tag: dict[int, Counter[int | None]] = defaultdict(Counter)
    for index, face in enumerate(faces):
        by_photo[face.image_id].append(index)
        if face.image_id not in images_of_tag[face.tag_id]:
            images_of_tag[face.tag_id].add(face.image_id)
            photo = photos.get(face.image_id)
            albums_of_tag[face.tag_id][photo.album_id if photo else None] += 1

    def ev_of(index: int) -> Evidence | None:
        sample = faces[index].sample
        return evidence[sample] if sample is not None else None

    tag_identity = analysis.tag_identity
    scored: dict[int, list[Reason]] = defaultdict(list)

    for index, face in enumerate(faces):
        ev = ev_of(index)
        person = face_person(analysis, face)
        sample = samples[face.sample] if face.sample is not None else None
        if ev and ev.own_support is not None:
            other = identity_name(analysis, ev.other_identity)
            margin = (ev.other_support or -1.0) - ev.own_support
            if ev.other_support is not None and margin > 0 and ev.other_support >= threshold:
                points = POINTS["closer_to_other_base"] + POINTS["closer_to_other_extra"] * min(margin / POINTS["margin_full"], 1.0)
                scored[index].append(Reason(
                    "closer_to_other", points,
                    f"Looks more like {other} (similarity {ev.other_support:.2f}) than like the other "
                    f"photos of {person} (similarity {ev.own_support:.2f}).",
                ))
            elif ev.own_support < threshold:
                points = POINTS["unsupported_base"] + POINTS["unsupported_extra"] * min((threshold - ev.own_support) / POINTS["unsupported_full"], 1.0)
                scored[index].append(Reason(
                    "unsupported", points,
                    f"Weak match with the other photos of {person}: similarity {ev.own_support:.2f}, "
                    f"below {threshold:.2f}.",
                ))
        if sample is not None:
            trained_as = tag_identity.get(face.tag_id)
            if trained_as is not None and trained_as != sample.identity:
                scored[index].append(Reason(
                    "mismatch", POINTS["mismatch"],
                    f"The face was learned as {identity_name(analysis, sample.identity)} but is tagged {person}.",
                ))

    for image_id, members in by_photo.items():
        per_tag: dict[int, list[int]] = defaultdict(list)
        for index in members:
            per_tag[faces[index].tag_id].append(index)
        for tag, group in per_tag.items():
            if len(group) < 2:
                continue
            supports = {i: (ev_of(i).own_support if ev_of(i) and ev_of(i).own_support is not None else None) for i in group}
            known = {i: s for i, s in supports.items() if s is not None}
            weakest = min(known, key=known.get) if len(known) > 1 else None
            for i in group:
                extra = POINTS["duplicate_weakest"] if i == weakest else 0.0
                scored[i].append(Reason(
                    "duplicate", POINTS["duplicate"] + extra,
                    f"{analysis.tag_names.get(tag, 'This person')} is tagged {len(group)} times in this photo"
                    + (" and this is the face that matches least." if extra else "."),
                ))
        for a_pos, a in enumerate(members):
            for b in members[a_pos + 1:]:
                if faces[a].tag_id != faces[b].tag_id and iou(faces[a].box, faces[b].box) >= 0.5:
                    for mine, other in ((a, b), (b, a)):
                        scored[mine].append(Reason(
                            "overlap", POINTS["overlap"],
                            f"The same face area is also tagged as {face_person(analysis, faces[other])}.",
                        ))

    # Weak context hints: only added to a face that is already suspicious.
    for index in list(scored):
        face = faces[index]
        photo = photos.get(face.image_id)
        appearances = len(images_of_tag[face.tag_id])
        if appearances < min_appearances:
            continue
        person = face_person(analysis, face)
        here = albums_of_tag[face.tag_id].get(photo.album_id if photo else None, 0)
        if photo and here <= 1:
            scored[index].append(Reason(
                "context_album", POINTS["context"],
                f"{person} appears in {appearances} photos but in no other photo of this folder.",
            ))
        others = {faces[i].tag_id for i in by_photo[face.image_id]} - {face.tag_id}
        if others and all(len(images_of_tag[face.tag_id] & images_of_tag[o]) <= 1 for o in others):
            scored[index].append(Reason(
                "context_company", POINTS["context"],
                f"{person} was never tagged together with the other people in this photo anywhere else.",
            ))

    suspects = []
    for index, reasons in scored.items():
        ev = ev_of(index)
        suspect = Suspect(index, reasons, sum(r.points for r in reasons), ev.pulls if ev else 0)
        if ev and ev.pulls:
            suspect.reasons.append(Reason(
                "pulls", 0.0,
                f"It is the closest look-alike for {ev.pulls} face(s) of other people, so it may be "
                "causing wrong suggestions. Fixing it matters more.",
            ))
        suspects.append(suspect)
    suspects.sort(key=lambda s: (-s.priority, -s.points, s.face))
    return suspects


# ===========================================================================
# Report
# ===========================================================================
class FaceTable:
    """Collects the faces the report refers to, once each, with display data."""

    def __init__(self, analysis: Analysis, collection: Path | None):
        self.analysis, self.collection = analysis, collection
        self.rows: list[dict[str, Any]] = []
        self.index: dict[int, int] = {}
        self.by_image: dict[int, list[int]] = defaultdict(list)
        for i, face in enumerate(analysis.faces):
            self.by_image[face.image_id].append(i)

    def position(self, face_index: int) -> str:
        """Where the face sits among the tagged faces of its photo, to help find it."""
        siblings = self.by_image[self.analysis.faces[face_index].image_id]
        if len(siblings) == 1:
            return "the only tagged face in the photo"
        centre = lambda i: self.analysis.faces[i].box[0] + self.analysis.faces[i].box[2] / 2
        rank = sorted(siblings, key=centre).index(face_index) + 1
        suffix = "th" if 10 <= rank % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(rank % 10, "th")
        text = f"{rank}{suffix} from the left of {len(siblings)} tagged faces"
        return text + (" (leftmost)" if rank == 1 else " (rightmost)" if rank == len(siblings) else "")

    def add(self, face_index: int) -> int:
        if face_index in self.index:
            return self.index[face_index]
        a = self.analysis
        face = a.faces[face_index]
        photo = a.photos.get(face.image_id)
        path = uri = None
        exists = False
        if photo and self.collection:
            file = self.collection / photo.album_path / photo.name if photo.album_path else self.collection / photo.name
            path, exists = str(file), file.is_file()
            uri = file.resolve().as_uri() if exists else None
        row = {
            "person": face_person(a, face),
            "tagPath": a.tag_paths.get(face.tag_id, ""),
            "imageId": face.image_id,
            "album": photo.album_path if photo else "",
            "file": photo.name if photo else f"(image #{face.image_id} not in the database)",
            "date": photo.date if photo else None,
            "box": list(face.box),
            "size": list(photo.display_size) if photo and photo.display_size else None,
            "position": self.position(face_index),
            "path": path,
            "uri": uri,
            "exists": exists,
        }
        self.rows.append(row)
        self.index[face_index] = len(self.rows) - 1
        return self.index[face_index]


def neighbour_json(table: FaceTable, pairs: list[tuple[int, float]]) -> list[dict[str, Any]]:
    a = table.analysis
    result = []
    for sample_index, sim in pairs:
        sample = a.samples[sample_index]
        entry: dict[str, Any] = {"similarity": round(sim, 3), "face": None, "person": identity_name(a, sample.identity)}
        if sample.face is not None:
            entry["face"] = table.add(sample.face)
        result.append(entry)
    return result


def build_report(
    analysis: Analysis, evidence: list[Evidence], suspects: list[Suspect], *,
    collection: Path | None, top: int, min_score: float, options: dict[str, Any],
) -> dict[str, Any]:
    table = FaceTable(analysis, collection)
    chosen = [s for s in suspects if s.points >= min_score][:top]
    items = []
    for rank, suspect in enumerate(chosen, 1):
        face = analysis.faces[suspect.face]
        ev = evidence[face.sample] if face.sample is not None else None
        items.append({
            "rank": rank,
            "face": table.add(suspect.face),
            "score": round(suspect.points, 2),
            "priority": round(suspect.priority, 2),
            "pulls": suspect.pulls,
            "reasons": [{"code": r.code, "text": r.text, "points": round(r.points, 2)} for r in suspect.reasons],
            "ownName": face_person(analysis, face),
            "otherName": identity_name(analysis, ev.other_identity) if ev and ev.other_identity is not None else None,
            "own": neighbour_json(table, ev.own_nearest) if ev else [],
            "other": neighbour_json(table, ev.other_nearest) if ev else [],
            "eraCount": ev.era_count if ev else 0,
        })
    orphans = [s for s in analysis.samples if s.face is None]
    per_identity = Counter(identity_name(analysis, s.identity) for s in orphans)
    trained = sum(1 for f in analysis.faces if f.sample is not None)
    return {
        "meta": {
            "schema": SCHEMA_VERSION,
            "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "confirmedFaces": len(analysis.faces),
            "trainedFaces": trained,
            "untrainedFaces": len(analysis.faces) - trained,
            "trainingRows": len(analysis.samples),
            "orphanRows": len(orphans),
            "identities": len({s.identity for s in analysis.samples}),
            "flagged": len([s for s in suspects if s.points >= min_score]),
            "shown": len(items),
            "model": analysis.model_info,
            "options": options,
            "collection": str(collection) if collection else None,
            "warnings": analysis.warnings,
        },
        "faces": table.rows,
        "suspects": items,
        "orphans": [{"person": name, "count": count} for name, count in per_identity.most_common(30)],
    }


def write_report(report: dict[str, Any], directory: Path) -> tuple[Path, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    # "<" is escaped so the data can never close the <script> element.
    payload = payload.replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    template = TEMPLATE.read_text(encoding="utf-8")
    if "/*__DATA__*/" not in template:
        raise SuspectError(f"Report template {TEMPLATE} has no data placeholder.")
    html_path = directory / "suspect_tags.html"
    html_path.write_text(template.replace("/*__DATA__*/null", payload, 1), encoding="utf-8")
    csv_path = directory / "suspect_tags.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "priority", "score", "lookalike_for", "person", "album_folder", "file", "face_position", "date", "reasons", "full_path"])
        for item in report["suspects"]:
            face = report["faces"][item["face"]]
            writer.writerow([
                item["rank"], item["priority"], item["score"], item["pulls"], face["person"], face["album"],
                face["file"], face["position"], face["date"] or "", " | ".join(r["text"] for r in item["reasons"]), face["path"] or "",
            ])
    return html_path, csv_path


# ===========================================================================
# Command line
# ===========================================================================
def run(args: argparse.Namespace) -> int:
    settings = resolve_settings(args)
    out_dir = args.output_dir.expanduser().resolve()
    if out_dir in {settings.db.expanduser().resolve().parent, settings.faces_db.expanduser().resolve().parent}:
        raise SuspectError("Choose an output folder that is not the digiKam database folder.")
    if settings.collection is not None and not settings.collection.is_dir():
        print(f"Note: collection folder not found ({settings.collection}); the report will have no images.", file=sys.stderr)
        settings.collection = None
    if settings.collection is None:
        print(f"Note: {ENV_COLLECTION} is not set; the report will list files but show no images.", file=sys.stderr)

    core, rec = connect_readonly(settings.db), connect_readonly(settings.faces_db)
    try:
        analysis = load_analysis(core, rec)
    finally:
        core.close()
        rec.close()

    trained = sum(1 for f in analysis.faces if f.sample is not None)
    orphans = sum(1 for s in analysis.samples if s.face is None)
    print(f"{len(analysis.faces):,} confirmed faces, {len(analysis.samples):,} training rows; "
          f"{trained:,} faces linked to their training row, {orphans:,} training rows without a live face.")
    if not analysis.samples:
        raise SuspectError("recognition.db contains no training rows yet; confirm some faces in digiKam first.")
    if trained == 0:
        raise SuspectError(
            "No confirmed face could be linked to a training row, so nothing can be located. "
            "The face hash may differ in your digiKam version; please report this."
        )
    if trained < 0.3 * len(analysis.samples):
        analysis.warnings.append(
            f"Only {trained:,} of {len(analysis.samples):,} training rows could be linked to a photo. "
            "Either many faces were re-tagged or moved since training, or the face hash differs in your digiKam version."
        )
    if args.check:
        for warning in analysis.warnings:
            print(f"Note: {warning}", file=sys.stderr)
        return 0

    print("Comparing faces...", file=sys.stderr)
    evidence = compute_evidence(
        analysis, max_ref=args.max_ref, candidates=args.candidates, era_days=args.era_days,
        threshold=args.match_threshold, progress=True,
    )
    suspects = score_faces(
        analysis, evidence, threshold=args.match_threshold, era_days=args.era_days, min_appearances=args.min_appearances,
    )
    options = {
        "matchThreshold": args.match_threshold, "eraDays": args.era_days, "maxRef": args.max_ref,
        "candidates": args.candidates, "minScore": args.min_score, "top": args.top,
    }
    report = build_report(analysis, evidence, suspects, collection=settings.collection, top=args.top,
                          min_score=args.min_score, options=options)
    html_path, csv_path = write_report(report, out_dir)
    for warning in analysis.warnings:
        print(f"Note: {warning}", file=sys.stderr)
    meta = report["meta"]
    print(f"{meta['flagged']:,} faces look suspicious; the {meta['shown']:,} most important are in the report.")
    print(f"Open {html_path} in your browser (double-click it; it must be opened from disk to show your photos).")
    print(f"Spreadsheet version: {csv_path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--env-file", type=Path, help="Settings file (default: .env in the current folder or the repository root)")
    parser.add_argument("--db", type=Path, help=f"digikam4.db (overrides {ENV_DB})")
    parser.add_argument("--faces-db", type=Path, help=f"recognition.db (overrides {ENV_FACES_DB}; default: next to digikam4.db)")
    parser.add_argument("--collection", type=Path, help=f"Image collection folder (overrides {ENV_COLLECTION})")
    parser.add_argument("-o", "--output-dir", type=Path, default=Path(__file__).resolve().parent.parent / "reports",
                        help="Where to write suspect_tags.html and .csv (default: reports/)")
    parser.add_argument("--check", action="store_true", help="Only load the data and print how well faces link to training rows")
    parser.add_argument("--top", type=int, default=150, help="Show at most this many faces (default 150)")
    parser.add_argument("--min-score", type=float, default=2.0, help="Ignore faces scoring below this (default 2.0)")
    parser.add_argument("--match-threshold", type=float, default=MATCH_THRESHOLD,
                        help=f"Cosine similarity that means 'same person' (default {MATCH_THRESHOLD}); raise for fewer, surer flags")
    parser.add_argument("--era-days", type=int, default=180, help="Compare with photos of the same person within +/- this many days (default 180)")
    parser.add_argument("--max-ref", type=int, default=400,
                        help="Compare against at most this many faces per person; larger people are sampled evenly. 0 = all (slower)")
    parser.add_argument("--candidates", type=int, default=5, help="How many look-alike people to test each face against (default 5)")
    parser.add_argument("--min-appearances", type=int, default=5, help="Photos a person needs before folder/company hints apply (default 5)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except (SuspectError, sqlite3.Error, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
