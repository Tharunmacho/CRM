# Page-sorting accuracy gate

A scan that holds several documents is cut into resume, passport, certificates and so on by what is on each page
(`split_by_content` in `backend/app/services/bulk_candidate_import.py`). Any change to the OCR, the page classifiers
(`app/integrations/ocr/classification.py`, `kind_classifier.py`) or the splitter can make that sorting worse without any
unit test noticing. The gate measures it on real bundles, page by page, and fails when it got worse.

Two parts:

| Part | Where | What it guards | Runs |
|---|---|---|---|
| Real-bundle gate | `backend/scripts/score_page_sorting.py` | accuracy on real recruiter files | by hand, before a deploy |
| Synthetic suite | `backend/tests/test_page_sort_gate.py` | the wiring reader -> classifiers -> splitter, on invented bundles | in the normal test suite (skipped without Tesseract) |

Real bundles, their truth files and the cached page texts hold real people's data. They live in
`backend/data/sort_bundles/`, which is git-ignored (`backend/data/`). **Never commit them**: the repository is public. Only
code and this document are committed.

## Before a deploy

From `backend/`:

```
python scripts/score_page_sorting.py --max-seconds 280 --json ../reports/sort-gate.json
```

* Reads every bundle with the real OCR (one bundle at a time; about 10 to 60 s for a small one, minutes for the whole set), runs the real
  splitter, prints one line per bundle and every wrongly filed page, then `GATE PASSED` or `GATE FAILED`.
* Exit code `0` passed, `1` below a threshold, `2` could not run (no bundles folder, no Tesseract, no case matched), `3` incomplete (worker-path bundles still being read: run again with `--resume`).
* `--saved` re-scores from the page texts cached by the last live run: no OCR, no PDF, a few seconds. Use it to iterate on a
  classifier. It does not test OCR changes: after changing OCR, preprocessing or languages, run live.
* `python scripts/score_page_sorting.py "name part" -v` runs the cases whose name contains the words and lists every page.
* `python -m pytest tests/test_page_sort_gate.py` is the quick synthetic check.

Thresholds (change with the options; these are the agreed defaults):

| Option | Default | Fails when |
|---|---|---|
| `--min-total` | 0.90 | right pages / scored pages over all bundles is lower |
| `--min-bundle` | 0.70 | any one bundle is lower (a truth file may set its own floor, see below) |
| `--max-seconds` | 0 (no limit) | a bundle took longer (live runs only). Use 280 for the usual limit |

A bundle also fails when the page reading failed or was cut short (the splitter stored the scan whole or left pages unread),
whatever its pages scored. Do not deploy on a failure: find which page moved (the table lists it with the truth and what it
was filed as), fix the code, run again.

## Which reading path is scored

In production a scan of `SORT_RESUMABLE_MIN_PAGES` (10) or more pages that needs OCR is not read in one go. The worker sorts it page by page
(`backend/app/services/page_sort.py`: units of `SORT_UNIT_PAGES` pages, each page saved the moment it is read, passes of `SORT_SLICE_SECONDS`,
a total cap of `SORT_TOTAL_SECONDS`), and only then classifies and splits. The gate reads such bundles the same way (`page_sort.sort_file`
with an in-memory store, no database), then runs the same classification and scoring. Shorter bundles, and bundles with a text layer on every
page, are read with `split_by_content`, as in production.

* `--worker-path` forces the worker reading for every bundle; `--splitter-path` forces the one-go reading (the old behaviour).
* A pass starts no new unit after `--chunk-seconds` (default 180), so one invocation fits a time limit. When pages are left the bundle shows
  `IN PROGRESS`, the exit code is 3 and the pages read so far are saved in `.cache/<case>.worker.json`. Run the same command with `--resume`
  until it prints the score. Without `--resume` a bundle starts over.
* `--budget-seconds N` caps one invocation over several bundles: bundles that do not fit wait for the next `--resume`.
* The seconds in the table are the reading seconds summed over all passes (what the worker's total cap counts). The cap is the app's own
  (`SORT_TOTAL_SECONDS`): a page it cuts off counts as wrong and the bundle as failed ("ran out of time"), as in production.

## Truth files

A case is two files in the bundles folder (`$PAGE_SORT_BUNDLES`, default `backend/data/sort_bundles`):

* `<case>.truth.txt`: what every page is.
* `<case>.pdf`, or `<case>.pdf.path`, a one-line text file with the absolute path of the PDF, so a real file stays where it is and is
  not copied.

```
# comments start with '#'
pages 1-2: resume
page 3: passport
pages 4,6: education_certificate
page 5: resume+experience_certificate    # '+' = the page must be filed under both kinds
page 7: photo                            # ignored by the score (photos stay next to the passport by design)
page 8: other                            # right when no kind was found for it
page 9: visa                             # also accepts visa_stamping
min-score: 0.55                          # optional floor for this bundle instead of --min-bundle
```

Kinds are the names the splitter files under: `resume`, `passport`, `aadhaar`, `education_certificate`, `experience_certificate`,
`medical`, `signed_offer`, `visa`, `other`, `photo` and the rest of `kind_classifier.py`. A page that is not listed is not scored. A page
is right when a piece containing it carries its true kind.

## Adding a case when a recruiter reports a wrong page

1. Put the PDF in `backend/data/sort_bundles/` as `<case>.pdf` (any name that does not identify the person is better), or keep it
   where it is and write its absolute path into `<case>.pdf.path`.
2. Write `<case>.truth.txt` by looking at the pages: one rule per page or run of pages. Cover the whole bundle, not only the page that was
   wrong, so the fix cannot move the error elsewhere.
3. `python scripts/score_page_sorting.py "<case>" -v`. It must reproduce the reported mistake (the page shows as `XX`).
4. Fix the code. Re-score that case, then run the whole gate (live) and check the total did not fall and no other bundle lost a page.
5. Commit only code (and a synthetic test for the pattern when one can be invented). Never the PDF, the truth file or `.cache/`.

If a bundle is known to be weak and the fix is not done yet, give it its own `min-score:` instead of lowering `--min-bundle` for
everyone, and raise that floor as it improves.

## One-time import of the owner's page maps

The existing hand-made `_page-map.txt` files (`pN -> Person-Kind.pdf` lines, optionally prefixed by the source file) are converted
by:

```
python scripts/score_page_sorting.py --import-page-maps M:\crm\candidates_organized --pdf-dir <folder with the raw bundles> [--pdf-dir ...]
```

It writes `<case>.truth.txt` and a `<case>.pdf.path` pointer for every source file whose PDF it finds under the `--pdf-dir` folders by
file name. Nothing is copied. A map that names no source file (older maps) gets a truth file only: write its `.pdf.path` by hand. A map
for a person with several files gives one case per file (`<person> - <file>`); sources with fewer than `--min-pages` (3) mapped pages are
skipped. In the maps an "Other" page described as a photo becomes `photo`, and an "Other" page described as a visa becomes `visa`.
Existing truth files are kept unless `--overwrite` is given.

`--import-saved-texts <folder>` loads page-text files of the older scratch format (`{"person": ..., "pages": [...]}`) into the cache,
so `--saved` works at once without OCR.

## Cache and settings

* Each live run saves the page texts it read to `<bundles>/.cache/<case>.json` with the PDF's SHA-256; `--saved` refuses a cache of a
  different PDF. Delete `.cache/` to force a re-read. Under `--saved` a case with no cache is listed as skipped, not scored.
* The gate uses the real settings (`app/core/config.py`, `.env`): the same OCR languages, script passes and time limits as the app.
  It never opens a database connection.
* Memory: the OCR runs as it does in the app. Run one gate at a time, and not next to the whole test suite on a small machine.
