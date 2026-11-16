from __future__ import annotations

import hashlib
import json
import random
import re
import sqlite3
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "suspects"))
import find_suspects as fs  # noqa: E402

DIMS = 128
RECOGNITION_SCHEMA = """
CREATE TABLE Identities (id INTEGER PRIMARY KEY, type INTEGER);
CREATE TABLE IdentityAttributes (id INTEGER, attribute TEXT, value TEXT);
CREATE TABLE FaceMatrices (id INTEGER PRIMARY KEY, identity INTEGER NOT NULL, removeHash TEXT NOT NULL, embedding BLOB NOT NULL);
CREATE TABLE Settings (keyword TEXT NOT NULL UNIQUE, value TEXT);
"""


def unit_vector(rng: random.Random) -> list[float]:
    vec = [rng.gauss(0, 1) for _ in range(DIMS)]
    return list(fs.unit(vec))


def near(center: list[float], rng: random.Random, noise: float = 0.25) -> list[float]:
    return list(fs.unit([c + rng.gauss(0, noise / DIMS ** 0.5) for c in center]))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Fixture:
    """digikam4.db + recognition.db with three people and a few planted problems."""

    def __init__(self, directory: Path, weird_name: str | None = None):
        self.dir = directory
        self.collection = directory / "Pictures"
        (self.collection / "Trip").mkdir(parents=True)
        self.core_path = directory / "digikam4.db"
        self.rec_path = directory / "recognition.db"
        self.core = sqlite3.connect(self.core_path)
        self.core.executescript((ROOT / "schema.sql").read_text())
        self.rec = sqlite3.connect(self.rec_path)
        self.rec.executescript(RECOGNITION_SCHEMA)
        self.rng = random.Random(7)
        self.next_image = 1
        self.core.execute("INSERT INTO AlbumRoots VALUES (1, 'Pictures', 0, 0, 'volumeid:?path=x', '/', 0)")
        for album, path in ((1, "/Trip"), (2, "/Home")):
            self.core.execute("INSERT INTO Albums (id, albumRoot, relativePath, date) VALUES (?, 1, ?, '2020-01-01')", (album, path))
        self.core.execute("INSERT INTO Tags (id, pid, name) VALUES (1, 0, 'People')")
        self.people = {}
        for tag, identity, key in ((2, 1, "Anna"), (3, 2, "Marc"), (4, 3, "Lea")):
            name = weird_name if (weird_name and key == "Anna") else key  # the displayed tag name
            self.core.execute("INSERT INTO Tags (id, pid, name) VALUES (?, 1, ?)", (tag, name))
            self.core.execute("INSERT INTO TagProperties VALUES (?, 'person', ?)", (tag, name))
            self.core.execute("INSERT INTO TagProperties VALUES (?, 'faceEngineUuid', ?)", (tag, f"uuid-{identity}"))
            self.rec.execute("INSERT INTO Identities VALUES (?, 1)", (identity,))
            self.rec.execute("INSERT INTO IdentityAttributes VALUES (?, 'uuid', ?)", (identity, f"uuid-{identity}"))
            self.rec.execute("INSERT INTO IdentityAttributes VALUES (?, 'name', ?)", (identity, name))
            self.people[key] = (tag, identity, unit_vector(self.rng))
        self.centers = {n: v[2] for n, v in self.people.items()}
        self.row = 1

    def photo(self, album=1, date="2020-06-01T10:00:00", create_file=False, size=(4000, 3000), orientation=1) -> int:
        image = self.next_image
        self.next_image += 1
        name = f"IMG_{image:04d}.jpg"
        self.core.execute("INSERT INTO Images (id, album, name, status, category) VALUES (?, ?, ?, 1, 1)", (image, album, name))
        self.core.execute("INSERT INTO ImageInformation (imageid, creationDate, orientation, width, height) VALUES (?, ?, ?, ?, ?)",
                          (image, date, orientation, size[0], size[1]))
        if create_file:
            (self.collection / ("Trip" if album == 1 else "Home")).mkdir(exist_ok=True)
            (self.collection / ("Trip" if album == 1 else "Home") / name).write_bytes(b"\xff\xd8not really a jpeg")
        return image

    def face(self, image, tag_name, vec=None, *, box=(100, 100, 200, 200), train=True, identity=None, raw=None) -> int:
        tag, ident, center = self.people[tag_name]
        xml = raw or fs.region_xml(*box)
        self.core.execute("INSERT INTO ImageTagProperties VALUES (?, ?, 'tagRegion', ?)", (image, tag, xml))
        if train:
            self.rec.execute(
                "INSERT INTO FaceMatrices (id, identity, removeHash, embedding) VALUES (?, ?, ?, ?)",
                (self.row, identity or ident, fs.face_hash(image, tag, xml), struct.pack(f"<{DIMS}f", *(vec or near(center, self.rng)))),
            )
            self.row += 1
        return image

    def populate(self):
        # 12 clean faces per person across two albums and two years.
        for name in ("Anna", "Marc", "Lea"):
            for i in range(12):
                self.face(self.photo(album=1 + i % 2, date=f"{2019 + i % 2}-0{1 + i % 9}-15T12:00:00"), name)
        # Planted error: a face that looks like Marc but was confirmed as Anna.
        self.bad_image = self.photo(album=1, create_file=True)
        self.face(self.bad_image, "Anna", near(self.centers["Marc"], self.rng), box=(500, 400, 300, 300))
        # Planted duplicate: Lea tagged twice in one photo (both look like Lea).
        self.dup_image = self.photo(album=2, create_file=True)
        self.face(self.dup_image, "Lea", box=(100, 100, 200, 200))
        self.face(self.dup_image, "Lea", box=(1500, 100, 200, 200))
        # Untrained face (confirmed but no embedding) and an orphan training row.
        self.face(self.photo(), "Marc", train=False)
        self.rec.execute(
            "INSERT INTO FaceMatrices (id, identity, removeHash, embedding) VALUES (?, 3, 'deadbeef', ?)",
            (self.row, struct.pack(f"<{DIMS}f", *near(self.centers["Anna"], self.rng))),
        )
        self.row += 1
        self.rec.execute("INSERT INTO Settings VALUES ('ExtractorModel', 'SFace')")
        self.commit()

    def commit(self):
        self.core.commit()
        self.rec.commit()

    def close(self):
        self.core.close()
        self.rec.close()


def run_tool(*argv: str) -> tuple[int, str, str]:
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = fs.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class HashTests(unittest.TestCase):
    def test_nibble_string(self):
        self.assertEqual(fs.nibble_string(0), "a" * 16)
        self.assertEqual(fs.nibble_string(1), "b" + "a" * 15)
        self.assertEqual(fs.nibble_string(0x10), "ab" + "a" * 14)
        self.assertEqual(fs.nibble_string(0xABC), "mlk" + "a" * 13)
        self.assertEqual(fs.nibble_string(-1), "p" * 16)

    def test_hash_is_sha1_of_the_documented_concatenation(self):
        xml = '<rect x="1" y="2" width="3" height="4"/>'
        expected = hashlib.sha1(("baaaaaaaaaaaaaaa" + "caaaaaaaaaaaaaaa" + xml).encode()).hexdigest()
        self.assertEqual(fs.face_hash(1, 2, xml), expected)

    def test_region_parsing(self):
        self.assertEqual(fs.parse_region('<rect x="10" y="20" width="30" height="40"/>'), (10, 20, 30, 40))
        self.assertEqual(fs.parse_region('<rect  height="40" width="30" y="20" x="10" />'), (10, 20, 30, 40))
        self.assertIsNone(fs.parse_region('<rect x="1" y="2" width="0" height="5"/>'))
        self.assertIsNone(fs.parse_region("garbage"))
        self.assertIsNone(fs.parse_region(None))


class EnvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_parser(self):
        env = self.dir / ".env"
        env.write_text('# comment\nexport A=1\nB="two words"  \nC=\'x # y\'\nD=plain # trailing\n\nE=\n')
        self.assertEqual(fs.parse_env_file(env), {"A": "1", "B": "two words", "C": "x # y", "D": "plain", "E": ""})

    def test_parser_rejects_garbage(self):
        env = self.dir / ".env"
        env.write_text("this is not valid\n")
        with self.assertRaises(fs.SuspectError):
            fs.parse_env_file(env)

    def test_precedence_and_relative_paths(self):
        env = self.dir / ".env"
        env.write_text(f"{fs.ENV_DB}=data/digikam4.db\n{fs.ENV_COLLECTION}=/from/file\n")
        args = fs.build_parser().parse_args(["--env-file", str(env)])
        settings = fs.resolve_settings(args, {})
        self.assertEqual(settings.db, self.dir / "data/digikam4.db")           # relative to the .env file
        self.assertEqual(settings.faces_db, self.dir / "data/recognition.db")  # default: next to digikam4.db
        self.assertEqual(settings.collection, Path("/from/file"))
        settings = fs.resolve_settings(args, {fs.ENV_COLLECTION: "/from/env"})
        self.assertEqual(settings.collection, Path("/from/env"))               # environment beats the file
        args = fs.build_parser().parse_args(["--env-file", str(env), "--collection", "/from/cli"])
        self.assertEqual(fs.resolve_settings(args, {fs.ENV_COLLECTION: "/from/env"}).collection, Path("/from/cli"))

    def test_missing_database_setting(self):
        env = self.dir / "empty.env"
        env.write_text("")
        with self.assertRaises(fs.SuspectError):
            fs.resolve_settings(fs.build_parser().parse_args(["--env-file", str(env)]), {})


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.fx = Fixture(self.dir)
        self.fx.populate()
        self.fx.close()
        self.out = self.dir / "out"
        self.env = self.dir / "empty.env"   # an empty settings file: everything comes from the command line
        self.env.write_text("")

    def tearDown(self):
        self.temp.cleanup()

    def run_tool(self, *extra):
        return run_tool("--db", str(self.fx.core_path), "--collection", str(self.fx.collection),
                        "-o", str(self.out), "--env-file", str(self.env), *extra)

    def report(self) -> dict:
        html = (self.out / "suspect_tags.html").read_text(encoding="utf-8")
        payload = re.search(r"const DATA = (.*);\n\n// <geometry>", html, re.S).group(1)
        return json.loads(payload)

    def test_planted_error_is_ranked_first_with_its_location(self):
        code, out, err = self.run_tool()
        self.assertEqual(code, 0, err)
        report = self.report()
        first = report["suspects"][0]
        face = report["faces"][first["face"]]
        self.assertEqual(face["person"], "Anna")
        self.assertEqual((face["album"], face["file"]), ("Trip", f"IMG_{self.fx.bad_image:04d}.jpg"))
        self.assertEqual(face["position"], "the only tagged face in the photo")
        self.assertTrue(face["exists"])
        self.assertTrue(face["uri"].startswith("file://"))
        self.assertEqual(first["reasons"][0]["code"], "closer_to_other")
        self.assertEqual(first["otherName"], "Marc")
        self.assertGreater(first["score"], 5)
        self.assertTrue(all(n["person"] == "Marc" for n in first["other"]))

    def test_clean_faces_are_not_flagged_but_duplicates_are(self):
        self.assertEqual(self.run_tool()[0], 0)
        report = self.report()
        flagged = {report["faces"][s["face"]]["file"]: s for s in report["suspects"]}
        dup_name = f"IMG_{self.fx.dup_image:04d}.jpg"
        self.assertIn(dup_name, flagged)
        dup_reasons = [r["code"] for r in flagged[dup_name]["reasons"]]
        self.assertIn("duplicate", dup_reasons)
        self.assertEqual(len(report["suspects"]), 3, [(s["rank"], s["reasons"]) for s in report["suspects"]])  # the error + 2 duplicate faces
        self.assertEqual(sorted(report["faces"][s["face"]]["position"] for s in report["suspects"][1:]),
                         ["1st from the left of 2 tagged faces (leftmost)", "2nd from the left of 2 tagged faces (rightmost)"])

    def test_counts_orphans_and_untrained_faces(self):
        self.assertEqual(self.run_tool()[0], 0)
        meta = self.report()["meta"]
        self.assertEqual(meta["confirmedFaces"], 3 * 12 + 1 + 2 + 1)
        self.assertEqual(meta["untrainedFaces"], 1)
        self.assertEqual(meta["trainedFaces"], meta["confirmedFaces"] - 1)
        self.assertEqual(meta["orphanRows"], 1)
        self.assertEqual(meta["trainingRows"], meta["trainedFaces"] + 1)
        self.assertEqual(self.report()["orphans"], [{"person": "Lea", "count": 1}])
        self.assertEqual(meta["model"], {"ExtractorModel": "SFace"})

    def test_csv_matches_report(self):
        self.assertEqual(self.run_tool()[0], 0)
        lines = (self.out / "suspect_tags.csv").read_text(encoding="utf-8-sig").splitlines()
        self.assertEqual(lines[0].split(",")[0:3], ["rank", "priority", "score"])
        self.assertEqual(len(lines) - 1, len(self.report()["suspects"]))
        self.assertIn("IMG_", lines[1])

    def test_never_modifies_the_digikam_databases(self):
        before = {p: sha256(p) for p in (self.fx.core_path, self.fx.rec_path)}
        files_before = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(self.run_tool()[0], 0)
        self.assertEqual({p: sha256(p) for p in before}, before)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), sorted(files_before + ["out"]))

    def test_check_mode_writes_nothing(self):
        code, out, _ = self.run_tool("--check")
        self.assertEqual(code, 0)
        self.assertIn("linked to their training row", out)
        self.assertFalse(self.out.exists())

    def test_without_collection_it_still_lists_files(self):
        code, _, err = run_tool("--db", str(self.fx.core_path), "-o", str(self.out), "--env-file", str(self.env))
        self.assertEqual(code, 0, err)
        report = self.report()
        self.assertIsNone(report["meta"]["collection"])
        self.assertFalse(report["faces"][report["suspects"][0]["face"]]["exists"])

    def test_era_support_uses_photos_from_the_same_period(self):
        analysis = fs.load_analysis(fs.connect_readonly(self.fx.core_path), fs.connect_readonly(self.fx.rec_path))
        evidence = fs.compute_evidence(analysis, max_ref=0, candidates=3, era_days=400, threshold=fs.MATCH_THRESHOLD)
        dated = [e for e in evidence if e.era_count]
        self.assertTrue(dated)
        self.assertTrue(all(e.own_support >= e.own_global for e in evidence if e.own_support is not None))

    def test_main_reports_actionable_errors(self):
        code, _, err = run_tool("--db", str(self.dir / "missing.db"), "--env-file", str(self.env))
        self.assertEqual(code, 2)
        self.assertIn("Database not found", err)
        code, _, err = run_tool("--env-file", str(self.env))
        self.assertEqual(code, 2)
        self.assertIn(fs.ENV_DB, err)


class SchemaAndSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_old_recognition_schema_is_explained(self):
        fx = Fixture(self.dir)
        fx.rec.executescript("DROP TABLE FaceMatrices; CREATE TABLE FaceMatrices (id INTEGER PRIMARY KEY, identity INTEGER, context TEXT, embedding BLOB);")
        fx.commit()
        fx.close()
        env = self.dir / "e.env"
        env.write_text("")
        code, _, err = run_tool("--db", str(fx.core_path), "-o", str(self.dir / "o"), "--env-file", str(env))
        self.assertEqual(code, 2)
        self.assertIn("removeHash", err)

    def test_data_can_never_close_the_script_element(self):
        fx = Fixture(self.dir, weird_name="</script><script>alert(1)</script>")
        fx.populate()
        fx.close()
        env = self.dir / "e.env"
        env.write_text("")
        out = self.dir / "o"
        code, _, err = run_tool("--db", str(fx.core_path), "-o", str(out), "--env-file", str(env))
        self.assertEqual(code, 0, err)
        html = (out / "suspect_tags.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("\\u003c/script>", html)  # "<" is escaped, so no closing tag can appear
        self.assertEqual(html.count("</script>"), 1)

    def test_rotated_photo_reports_display_size(self):
        fx = Fixture(self.dir)
        image = fx.photo(orientation=6, size=(4000, 3000))
        fx.face(image, "Anna")
        fx.commit()
        fx.close()
        analysis = fs.load_analysis(fs.connect_readonly(fx.core_path), fs.connect_readonly(fx.rec_path))
        self.assertEqual(analysis.photos[image].display_size, (3000, 4000))


if __name__ == "__main__":
    unittest.main()
