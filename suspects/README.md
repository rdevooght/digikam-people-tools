# Suspect face tags

digiKam learns to recognise people from the faces you **confirm**. If one confirmation is wrong, that wrong face becomes a reference, and similar faces of the wrong person get suggested under the wrong name, which you may then confirm in turn. This tool looks for such mistakes so you can correct them in digiKam.

It reads digiKam's two SQLite databases, scores every confirmed face, and writes an HTML report (and a CSV) showing **which photo, which face, and why it looks wrong**. It never modifies the databases, and image files are only referenced by the report, never opened or changed.

## Quick start

```bash
cp .env.example .env          # then edit the three paths
uv run suspects/find_suspects.py --check   # optional sanity check, writes nothing
uv run suspects/find_suspects.py
```

Open `reports/suspect_tags.html` by double-clicking it. Open it **from disk**, not through a web server: the page shows your photos through `file://` links, which browsers block from `http://` pages.

Close digiKam first, or work on copies of the databases (include any `-wal` file). Python 3.10+, standard library only.

### Settings

| Setting | Meaning |
|---|---|
| `DIGIKAM_DB` | Path to `digikam4.db` (required) |
| `DIGIKAM_FACES_DB` | Path to `recognition.db` (default: next to `digikam4.db`) |
| `DIGIKAM_COLLECTION` | Folder of your photos, the album root (optional; without it no images are shown) |

Precedence: command line (`--db`, `--faces-db`, `--collection`), then environment variables, then `.env` (searched in the current folder, then the repository root; or `--env-file`). Relative paths in a `.env` file are relative to that file. If your collection has several album roots, the one collection folder is used for all of them, and photos that are not found show "File not found".

## Reading the report

Cards are ordered by **priority**: suspicion score × (1 + ln(1 + look-alike count)). Each card shows the cropped face, the whole photo with the face outlined, the **folder and file name** to open in digiKam, **where** the face is among the tagged faces of that photo, and the reasons. "Compare with similar faces" shows the person's closest other faces next to the closest look-alike of someone else, which is usually enough to decide.

Then correct it in digiKam as you normally do, and tick **Reviewed** (kept in your browser, hidden on request). Re-run the tool afterwards to check the result.

## How faces are scored

Every confirmed face has a learned embedding in `recognition.db` (128 numbers, from the SFace model). The tool links each embedding back to its photo through digiKam's own *face hash* (`removeHash`, a SHA-1 of image id, tag id and face box), then compares embeddings by cosine similarity.

| Signal | Points | Idea |
|---|---|---|
| Closer to someone else | 2 to 6 | The face resembles another person (similarity at least the match threshold) more than the other faces of its own person. "Support" is the mean of the 3 most similar faces, so one odd neighbour does not decide. |
| Weakly supported | 1 to 3 | No good match among the person's other faces (below the threshold). |
| Duplicate in a photo | 2, +2 for the weakest | The same person is tagged twice in one photo. |
| Overlapping tags | 3 | The same face area carries two different people. |
| Tag and learned identity differ | 2 | The training row names another person than the tag does. |
| Folder / company hints | 0.5 each | The person never appears elsewhere in this folder, or never with the other people in the photo. They only count for faces that are already suspicious. |

The comparison is **period-aware**: a face is also compared with the same person's faces within ±180 days (`--era-days`), and the better of the two is used, so children and people who changed over the years are not flagged just for ageing.

**Look-alike count** is how many other faces have this face as their closest face of any *other* person. A wrong face with a high count is the one spreading errors, so it is moved up.

All weights are named constants at the top of `find_suspects.py`.

### Training rows without a photo

Some learned faces no longer belong to a tagged face (photo deleted, face box moved, tag changed). They still influence recognition but cannot be located. The report counts them per person; digiKam's Maintenance tool for faces can rebuild the learned data from your confirmed faces (option names vary by version).

### Options

```text
--top N               show at most N faces (150)
--min-score S         ignore faces scoring below S (2.0)
--match-threshold T   cosine similarity that means "same person" (0.363); raise for fewer, surer flags
--era-days D          period window around each photo (180)
--max-ref N           compare with at most N faces per person, sampled evenly; 0 = all, slower (400)
--candidates N        how many look-alike people each face is tested against (5)
--min-appearances N   photos a person needs before folder/company hints apply (5)
--check               only load the data and report how well faces link to training rows
-o DIR                output folder (reports/)
```

## Limits to know about

- **Only trained faces get an embedding verdict.** digiKam skips tiny, noisy or blurry faces when learning, so those confirmed faces can only be flagged by the photo-based checks. The report says how many.
- **Unconfirmed suggestions are not stored with embeddings**, so they cannot be checked. Only confirmed faces are.
- **Large people are sampled** (`--max-ref`), so look-alike counts are approximate. Raise it for exactness at the cost of time (about a minute for 15,000 faces on a laptop-class CPU).
- **Resemblance is not identity.** Siblings, twins and children at different ages can legitimately look alike. Treat the report as a review queue, not a verdict.
- **Face boxes** are stored in the coordinates of the upright photo. If a file was rotated or edited after digiKam scanned it, the box can be misplaced; the card then says so.
- **The face hash was derived from digiKam 8.x source code.** Run with `--check` first: it prints how many training rows were linked to a photo. If that is very low, the hash differs in your version and the tool will say so rather than guess.

## Privacy

The report and CSV contain people's names, folder and file names. They are written to `reports/`, which is git-ignored. The page makes no network requests.

## Tests

```bash
uv run python -m unittest discover -s tests -v
node --test tests/suspect_report.test.js
```

The Python tests build small digiKam-like databases (core schema from `schema.sql`, face tables as digiKam creates them) with a planted mistake, a duplicate, an orphan training row and an untrained face. They also check that the databases are byte-for-byte unchanged and that data can never break out of the report's `<script>` element.
