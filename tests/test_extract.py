from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from extract import ExtractionError, extract_database, parse_photo_date, tag_paths, write_data


class ExtractorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.database = self.directory / "digikam4.db"
        self.connection = sqlite3.connect(self.database)
        self.connection.executescript((ROOT / "schema.sql").read_text())
        self.connection.execute("INSERT INTO AlbumRoots VALUES (1, 'Test', 0, 0, 'test', '/', 0)")
        self.connection.execute("INSERT INTO Albums (id, albumRoot, relativePath, date) VALUES (1, 1, 'Album', '2019-06-01')")
        self.tag(1, 0, "People")
        self.tag(2, 1, "Alice", person=True)
        self.tag(3, 1, "Bob", person=True)
        self.tag(4, 1, "Unused", person=True)
        self.tag(5, 1, "Unknown")
        self.connection.execute("INSERT INTO TagProperties VALUES (5, 'unknownPerson', '')")
        self.tag(6, 0, "Landscape")
        self.connection.commit()

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def tag(self, tid, pid, name, person=False):
        self.connection.execute("INSERT INTO Tags (id, pid, name) VALUES (?, ?, ?)", (tid, pid, name))
        if person:
            self.connection.execute("INSERT INTO TagProperties VALUES (?, 'person', ?)", (tid, name))

    def photo(self, image_id, tags, creation="2020-01-10T12:00:00", album=1):
        self.connection.execute("INSERT INTO Images (id, album, name, status, category) VALUES (?, ?, ?, 1, 1)", (image_id, album, f"{image_id}.jpg"))
        self.connection.execute("INSERT INTO ImageInformation (imageid, creationDate) VALUES (?, ?)", (image_id, creation))
        for tid in tags:
            self.connection.execute("INSERT OR IGNORE INTO ImageTags VALUES (?, ?)", (image_id, tid))
        self.connection.commit()

    def export(self, **kwargs):
        return extract_database(self.database, **kwargs)

    def test_counts_dates_and_people_scope(self):
        self.photo(1, [2, 3, 6])
        self.photo(2, [2], "2020-03-02T09:00:00")
        self.photo(3, [5, 6])
        data = self.export()
        self.assertEqual(data["meta"]["peopleCount"], 3)
        self.assertEqual(data["meta"]["taggedPhotos"], 2)
        self.assertEqual(data["meta"]["tagAssignments"], 3)
        self.assertEqual(data["meta"]["totalPhotos"], 3)
        self.assertEqual(data["months"], ["2020-01", "2020-02", "2020-03"])
        self.assertEqual({p["name"]: p["count"] for p in data["people"]}, {"Alice": 2, "Bob": 1, "Unused": 0})
        self.assertEqual(data["photos"], [[0, [2, 3]], [2, [2]]])
        self.assertEqual(data["meta"]["unknownTaggedPhotos"], 1)
        self.assertNotIn("Landscape", [p["name"] for p in data["people"]])

    def test_date_fallback_and_policies(self):
        self.photo(1, [2], None)
        self.photo(2, [2], "not-a-date")
        self.photo(3, [2], "2021-02-03T00:00:00")
        auto = self.export()
        self.assertEqual(auto["meta"]["albumDatePhotos"], 2)
        self.assertEqual(auto["meta"]["creationDatePhotos"], 1)
        creation = self.export(date_source="creation")
        self.assertEqual(creation["meta"]["undatedPhotos"], 2)
        album = self.export(date_source="album")
        self.assertEqual(album["meta"]["albumDatePhotos"], 3)

    def test_undated_still_counts_but_not_plotted(self):
        self.photo(1, [2, 3], None, album=999)
        data = self.export()
        self.assertEqual(data["months"], [])
        self.assertEqual(data["photos"], [[-1, [2, 3]]])
        self.assertEqual(data["meta"]["undatedPhotos"], 1)
        self.assertEqual(data["people"][0]["undated"], 1)
        self.assertIsNone(data["people"][0]["first"])

    def test_unknown_can_be_included(self):
        self.photo(1, [5])
        data = self.export(include_unknown=True)
        self.assertIn("Unknown", [p["name"] for p in data["people"]])
        self.assertEqual(data["meta"]["taggedPhotos"], 1)

    def test_nested_tags_and_property_only(self):
        self.tag(7, 1, "Family")
        self.tag(8, 7, "Carol", person=True)
        self.photo(1, [7, 8])
        self.assertEqual(self.export()["meta"]["tagAssignments"], 2)
        strict = self.export(person_property_only=True)
        self.assertEqual(strict["meta"]["tagAssignments"], 1)
        self.assertEqual(strict["people"][0]["path"], "People/Family/Carol")
        self.assertEqual(self.connection.execute("SELECT COUNT(*) FROM TagsTree WHERE id=8 AND pid=1").fetchone()[0], 1)

    def test_custom_root_by_id_and_path_is_strict(self):
        self.tag(7, 0, "Crew")
        self.tag(8, 7, "Dave", person=True)
        self.photo(1, [2, 8])
        by_id = self.export(people_root="7")
        by_path = self.export(people_root="Crew")
        self.assertEqual([p["name"] for p in by_id["people"]], ["Dave"])
        self.assertEqual(by_id["people"], by_path["people"])
        self.assertEqual(by_id["meta"]["rootIds"], by_path["meta"]["rootIds"])
        auto = self.export()
        self.assertIn("Dave", [p["name"] for p in auto["people"]])  # person property outside People
        strict_auto = self.export(person_property_only=True)
        self.assertIn("Dave", [p["name"] for p in strict_auto["people"]])

    def test_virtual_root_is_omitted_from_paths(self):
        self.connection.execute("DELETE FROM ImageTags")
        self.connection.execute("INSERT INTO Tags (id, pid, name) VALUES (99, -1, '_Digikam_root_tag_')")
        self.connection.execute("UPDATE Tags SET pid=99 WHERE id=1")
        self.connection.commit()
        self.photo(1, [2])
        data = self.export(people_root="People")
        self.assertEqual(data["people"][0]["path"], "People/Alice")

    def test_property_fallback_without_people_root(self):
        self.connection.execute("UPDATE Tags SET name='Faces' WHERE id=1")
        self.connection.commit()
        self.photo(1, [2])
        data = self.export()
        self.assertEqual(data["meta"]["peopleRoot"], "Tags marked person")
        self.assertEqual(data["meta"]["tagAssignments"], 1)

    def test_no_people_is_actionable_error(self):
        self.connection.execute("DELETE FROM TagProperties")
        self.connection.execute("DELETE FROM Tags WHERE id=1")
        self.connection.commit()
        with self.assertRaisesRegex(ExtractionError, "--list-roots"):
            self.export()

    def test_ambiguous_people_roots_require_override(self):
        self.tag(9, -1, "_Digikam_root_tag_")
        self.tag(7, 9, "People")
        self.tag(8, 7, "Dave", person=True)
        self.connection.commit()
        with self.assertRaisesRegex(ExtractionError, "Multiple People roots"):
            self.export()
        self.assertEqual(self.export(people_root="7")["people"][0]["name"], "Dave")

    def test_empty_collection_exports_valid_data(self):
        data = self.export()
        self.assertEqual(data["photos"], [])
        self.assertEqual(data["months"], [])
        self.assertEqual(data["meta"]["taggedPhotos"], 0)
        self.assertTrue(all(p["count"] == 0 for p in data["people"]))

    def test_database_is_not_modified(self):
        self.photo(1, [2, 3])
        before = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.export()
        self.assertEqual(before, hashlib.sha256(self.database.read_bytes()).hexdigest())
        connection = sqlite3.connect(self.database.as_uri() + "?mode=ro", uri=True)
        with self.assertRaises(sqlite3.OperationalError):
            connection.execute("DELETE FROM ImageTags")
        connection.close()

    def test_orphan_tag_is_retained_and_reported(self):
        self.connection.execute("INSERT INTO ImageTags VALUES (999, 2)")
        self.connection.commit()
        data = self.export()
        self.assertEqual(data["meta"]["orphanTaggedPhotos"], 1)
        self.assertEqual(data["meta"]["undatedPhotos"], 1)
        self.assertEqual(data["meta"]["taggedPhotos"], 1)

    def test_script_output_is_safe_and_reloadable(self):
        self.connection.execute("UPDATE Tags SET name='</script><img src=x onerror=alert(1)>' WHERE id=2")
        self.connection.commit()
        self.photo(1, [2])
        output = self.directory / "site" / "data.js"
        data = self.export()
        write_data(data, output)
        text = output.read_text()
        self.assertNotIn("</script>", text)
        self.assertIn("\\u003c", text)
        payload = text.split("window.DIGIKAM_DATA = ", 1)[1].rsplit(";\n", 1)[0]
        self.assertEqual(json.loads(payload)["people"][0]["count"], 1)
        self.assertEqual(list(output.parent.glob(".people-*")), [])

    def test_cli_list_roots(self):
        result = subprocess.run([sys.executable, str(ROOT / "extract.py"), str(self.database), "--list-roots"], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("People", result.stdout)
        self.assertIn("Landscape", result.stdout)

    def test_output_cannot_overwrite_source_database(self):
        before = self.database.read_bytes()
        result = subprocess.run([sys.executable, str(ROOT / "extract.py"), str(self.database), "--output", str(self.database)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("must not be the source database", result.stderr)
        self.assertEqual(before, self.database.read_bytes())

    def test_date_parser(self):
        self.assertEqual(parse_photo_date("2020-02-29T23:59:59.123"), "2020-02-29")
        self.assertEqual(parse_photo_date("2020-01-01"), "2020-01-01")
        self.assertIsNone(parse_photo_date("2020-02-30"))
        self.assertIsNone(parse_photo_date("0000-00-00"))
        self.assertIsNone(parse_photo_date(12345))

    def test_cycle_detection(self):
        tags = {1: {"pid": 2, "name": "A"}, 2: {"pid": 1, "name": "B"}}
        with self.assertRaisesRegex(ExtractionError, "Cycle"):
            tag_paths(tags, 1)


if __name__ == "__main__":
    unittest.main()
