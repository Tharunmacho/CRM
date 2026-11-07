# Testing a problem file with the OCR self-check

When an upload is "not read properly", run the local reader on that file and look at its structure. The tool
prints **only** counts, page kinds, confidences, reasons and yes/no results. It never prints, stores or sends the
text of a page, the numbers read from a document (passport, Aadhaar, phone, e-mail) or any page image, so support
can run it on a customer's file.

```
cd backend
.venv\Scripts\python.exe -m app.integrations.ocr.selfcheck "C:\path\to\file.pdf"
.venv\Scripts\python.exe -m app.integrations.ocr.selfcheck "C:\path\to\folder" --workers 4
.venv\Scripts\python.exe -m app.integrations.ocr.selfcheck file.pdf --json
```

It uses the same local inspection as the upload (no network, no database, no Veris call), so what it reports is
what the upload decides before anything is sent to Veris.

## What the report says

* **pages**: how many pages were read, blank, photo (a picture with almost no text, never blocks the file) or
  unread. These always add up to the page count; a page that is unread names its reason and how many reading
  settings were tried.
* **kinds**: the pages classified as resume, passport, Aadhaar, and the pages left unknown.
* **per page**: status, kind, confidence, which engine read it (`tesseract`, `rapidocr` or `both`), the setting that
  gave the best read, and seconds. A page that read badly on the first pass was read again under other settings
  (300 dpi, sharpening, contrast, a cut-out photographed card, a passport zone pass, an Aadhaar digits pass) and the
  reads were merged.
* **MRZ / Aadhaar**: whether a passport machine-readable zone or an Aadhaar number was found, and whether it passes
  its check digits (ICAO 9303 for the passport, Verhoeff for Aadhaar). "Found but checksum not valid" means the
  read has a wrong character: compare that page with the original.

Exit status is 0 when every page is accounted for, 1 when a page is unread, 2 when a file cannot be opened.

## If a page is unread or a pattern is missing

1. Read the reason on that page: faint, blurred, too small, or a script this reader does not have (only English
   is installed; add the language pack with `TESSERACT_CMD`/`OCR_LANGUAGES` if the document is in another script).
2. Re-run with a higher quality copy of the page, if there is one.
3. Report the line for the page (status, confidence, engine, seconds) to the developers; it is enough to find the
   cause without the document.

## Settings

| Setting | Meaning |
| --- | --- |
| `OCR_PAGE_WORKERS` | pages read at once in one document |
| `OCR_MAX_TESSERACT_PROCESSES` | the most Tesseract processes at once in the whole process, however many documents are being read (default: twice the CPU count) |
| `OCR_SECOND_ENGINE` | `off` turns the second engine (RapidOCR) off; Tesseract alone then reads |
| `OCR_SECOND_ENGINE_SLOTS` | simultaneous second-engine runs (default: half the CPUs) |
| `OCR_SECOND_ENGINE_SECONDS` | the longest a page waits for the second engine (default 40) |

The regression gate that stops this class of problem coming back is `backend/tests/test_ocr_quality_gate.py`.
