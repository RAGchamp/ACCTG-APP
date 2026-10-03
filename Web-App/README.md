# Small Business Accounting Agent - MVP 1

A local Flask app that keeps the books of a take-out and delivery pizza shop in Hartford, CT
(a single-member LLC). The owner scans receipts, card and bank statements, checks, handwritten
cash sheets, Square reports, DoorDash / Uber Eats / Grubhub statements and payroll reports.
**Claude** reads each document and proposes journal entries; the owner **reviews** them (accept,
edit, feedback, reject); accepted entries go to a strict double-entry **General Ledger**, from
which **code** (never Claude) produces the financial statements and draft tax worksheets.

Plan: `..\INFO\SMALL-BIZ-ACCTG-MVP1-PLAN.md` (decisions D1-D10 in §18).
Reuses the Annual Report Analyzer's engine (`..\..\ANN-RPT-ANALYZER\WEB-APP`): `claude_client.py`
(copied unchanged), `webcommon.py` (jobs), the page-image → Claude → JSON → checks loop of
`ingest\ocr_transcribe.py` / `ocr_quality.py`, and the SHA-256 dedupe of `rptpkg\store.py`.

## Run

```powershell
cd C:\Daya\RAGchamp\SMALL-BIZ-ACCTG\Web-App
pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5050. Requirements: Python 3.12 and the Claude Code CLI logged in
(`C:\Users\dayam\.local\bin\claude.exe`, or set `CLAUDE_EXE`). No API key is needed.

## How the owner uses it

1. **Setup** - check the business profile (card / bank last 4 digits, sales tax frequency), post the
   **opening balances** on 2026-01-01, adjust the Chart of Accounts if needed.
2. **Inbox** - upload PDFs / CSVs, or drop them into `SMALL-BIZ-ACCTG\Inbox\` and click ↻. Each file is
   hashed (the same scan is never added twice), its pages are rendered, and Claude finds every
   document in it (several receipts on one page are split) and extracts it as JSON. CSV exports
   (Square Sales Summary, bank, card) are imported exactly, without Claude. Code checks every
   document's arithmetic (items + tax + tip = total, opening + transactions = closing, Square and
   platform tie-outs, check amount in words vs figures, statement gaps).
3. **Review** - the page image on the left, the proposed entries on the right, with *why*, the
   **match hints** ("this card line is receipt #12, already booked to 2150 - no second expense"),
   Claude's questions and any failed checks. **Accept** (A), **Edit** (E), **Feedback…** (F, Claude
   revises the proposal and may suggest a vendor rule: *Remember this rule?*), **Reject**, or
   **Bulk accept** proposals with high confidence that passed every check. Review in order (J / K).
4. **Ledger** - every entry, its source image; **Reverse** an entry (posted entries are never
   changed; **Reverse** on a reversal undoes it: the reversed entry is posted again, *Reinstated* in the trail); manual entries (depreciation - a suggestion is shown - or the year-end inventory count).
   **Ledger trail** (link next to *Entries*, `/ledger-trail`): every change to the ledger - entries posted,
   reversed, months closed and reopened - with when, where in the app it came from, the reason and a snapshot
   of the lines; filter, CSV. Stored in the `ledger_trail` table (`books\trail.py`), append-only (the database
   refuses edits and deletes). Books made before it existed are back-filled once from the journal.
5. **Reconcile** - statement closing balance vs the GL for the bank (1010) and card (2100), the open
   items on the clearing accounts; **Close** the month (posting into it is then refused).
6. **Reports** - Income Statement (food cost %), Balance Sheet, Cash Flow (indirect), Owner's equity,
   Trial Balance, General Ledger; CSV and print. Each report checks itself (balances, ties).
7. **Taxes** - OS-114 worksheet per month (tax due vs tax collected per the books, marketplace
   sales deducted, use tax), Schedule C with Part III and Schedule SE, fixed assets (4562 figures),
   1099-NEC candidates, CT figures, the tax calendar, and the **CPA package** (zip). All drafts.
8. **Input docs** (Review input documents, `..\INFO\DOC-REVIEW-REPROCESS-PLAN.md`) - tick **Live documents**
   and/or **Sandbox documents**, tick any documents (up to 30 at a time) and click **Review**. Each document gets
   a box: the scan on the left; on the right the extracted data, the **ledger** lines (debit / credit, and what
   settled each clearing line), the **financial statement** lines and the **tax** lines they feed (computed by
   code, `reports\tagging.py`). Under it, **feedback** (what is wrong + free text) is saved as
   `feedback\<file-name>-<YYYYMMDD-HHMMSS>.json` (input file path, extracted data, tagging, feedback, and later
   the re-process result). Optional **re-process**: re-propose with the feedback (Claude reviser), re-extract
   (optionally at 300 dpi; the feedback goes to Claude as an owner note) or correct the extracted figures. A posted
   document is un-posted first by **reversing** its entries; documents booked against it (e.g. the card statement
   line that settled a receipt) block this unless *Re-process together* is ticked; a closed month must be
   reopened first. Nothing is posted: the new proposal waits on **Review**. In a replay sandbox, *Use live
   Claude for this* (ticked by default) makes that one call real. This screen never changes the open books.

### How money is kept from being booked twice (plan §4.2)

| Clearing account | Booked by | Settled by |
|---|---|---|
| 2150 Purchases awaiting statement | a receipt / check paid by card, debit or check | the card or bank statement line |
| 1100 Card sales clearing | the Square report (card sales net of fees) | the Square payout deposit |
| 1110 Platform receivable | the DoorDash / Uber Eats / Grubhub statement | the payout deposit |
| 1050 Undeposited funds | a customer check, a cash drop | the bank deposit |
| 2300 Payroll liabilities | the payroll provider report | the provider's bank debit |

`books\match.py` finds the open items and gives Claude **match hints**; `books\propose.py` refuses a
proposal that ignores them. A receipt that arrives after its statement is linked (*support only*).

## Synthetic test data (plan §15)

**Test data** screen, or the command line:

```powershell
python -m testdata list                                   # the 57 scenarios + simulation presets
python -m testdata coverage                               # every feature has a scenario
python -m testdata generate receipt-personal-item --difficulty messy --seed 7
python -m testdata simulate jan-2026-scanned              # a whole consistent month (73 documents)
python -m testdata evaluate jan-2026-scanned              # mocked: Claude replayed from the answer key
python -m testdata evaluate jan-2026-scanned --live       # real Claude calls (costs usage)
python -m testdata all                                    # every scenario, generated and evaluated
```

- The generator writes the **ledger truth** first (the real events), then every document those
  events create: thermal receipts, invoices, card and bank statements (PDF and CSV), Square reports,
  platform statements, payroll reports, handwritten cash sheets and checks. Scan effects (skew, blur,
  noise, fading, stains, creases, upside-down, phone photo) and handwriting (bundled OFL fonts:
  Caveat, Reenie Beanie, Shadows Into Light) by difficulty: `clean`, `scanned`, `messy`,
  `adversarial` (a digit really covered - the app must mark it `[?]` and ask), `photo`.
- Each set goes into a **sandbox** (`data\sandbox\<name>\`) with an `answer_key.json`: what Claude
  should read, the expected entries, the owner script, the expected reports, OS-114, Schedule C/SE and
  reconciliations - computed by posting the expected entries with the app's own ledger code.
- Every generated PDF says **SYNTHETIC TEST DATA** (metadata + footer); intake refuses synthetic files
  in the live books and real files in a sandbox. Same scenario + seed → byte-identical files.
- **Evaluate** runs the real pipeline and writes a scorecard (`testdata\results\*.html`): extraction
  field accuracy and *silently wrong* figures (must be 0), entries, matching and *double-counted*
  money (must be 0), reports, reconciliation, tax, Claude time and cost.
- To click through a sandbox yourself: open it (the orange bar shows you are in a sandbox), then
  **Inbox → Load the sandbox's generated files**, then **Review**.

## Tests

```powershell
python -m pytest -q                      # all 57 scenarios end to end + unit and web tests (mocked, ~2.5 min)
python -m pytest -q -m slow              # also the simulated January (73 documents)
python -m pytest -q -m live              # real Claude calls on generated documents (costs usage)
```

## Files

| Path | Purpose |
|---|---|
| `app.py`, `config.py` | Flask app; settings (paths, Claude CLI, DPI, capitalization threshold) |
| `claude_client.py` | `claude -p` (text) and `claude -p --tools Read` (page images) - copied from the analyzer |
| `ai.py` | Every Claude call: **live** or **replay** (answer key); saves each prompt and reply next to its document |
| `webcommon.py` | Background jobs, `UserError`, Markdown (from the analyzer) |
| `ingest\intake.py` | Upload / inbox, SHA-256 dedupe, synthetic-data guard, page images |
| `ingest\extract.py`, `prompts\extract*.txt`, `schemas\*.json` | Classifier + Extractor in one Claude call; statement continuity flags |
| `ingest\checks.py` | Arithmetic checks per document type (from the analyzer's tie-outs) |
| `ingest\csv_import.py` | Square / bank / card CSV exports, no Claude |
| `books\schema.sql`, `db.py`, `coa.py` | The books (integer cents, append-only triggers); the pizza-shop Chart of Accounts with Schedule C lines |
| `books\post.py` | Posting checks, reversal, period locks, balances |
| `books\match.py` | Open items on clearing accounts and match hints |
| `books\propose.py`, `prompts\bookkeeper*.txt`, `prompts\reviser.txt` | The Bookkeeper and Reviser, and the code checks on every proposal |
| `books\review.py`, `books\reconcile.py` | Accept / edit / reject / bulk / rules; reconciliation and month close |
| `reports\statements.py` | TB, IS, BS, CF (indirect), equity, GL |
| `tax\*.py`, `tax_rules\2026\*.yaml` | OS-114, Schedule C/SE, 4562 figures, 1099 list, CT figures and calendar; rules as data (verify yearly) |
| `web\pages.py`, `web\api.py`, `templates\`, `static\app.js` | The screens and their actions |
| `static\app.css`, `static\logo.svg`, `templates\_ui.html` | The look (`..\INFO\UI-REVAMP-PLAN.md`): navy top bar, centered menu, light-yellow overview box (`ui.overview`, `ui.tile`), light-blue headers, striped tables; light and dark |
| `web\doc_review.py`, `templates\doc_review*.html`, `static\doc_review.js` | Review input documents: list, boxes, feedback, re-process jobs |
| `reports\tagging.py` | Where a document landed: ledger lines, statement lines, tax lines (code only) |
| `books\feedback_files.py`, `books\review.py` (`unpost_document`) | Feedback JSON files; un-posting by reversal for re-processing |
| `testdata\` | Scenarios (YAML), `truth.py`, `render.py`, `degrade.py`, `generate.py`, `simulate.py`, `evaluate.py`, `coverage.py`, `fonts\` |
| `data\` (not in git) | `live\` (the real books) and `sandbox\<name>\` |
| `feedback\` (not in git) | One JSON file per piece of feedback from Review input documents |

## Limits of MVP 1 (see the plan)

- Tax worksheets are **drafts** for the CPA; nothing is e-filed. `tax_rules\2026\*.yaml` is marked
  `verified: false` until checked against the 2026 forms (the 2026 SS wage base and the 1099-NEC
  threshold especially). Open CT questions (marketplace sales on OS-114, delivery fee taxability) are
  listed on the worksheet.
- Single-member LLC only (Schedule C / SE); Form 1065 and the S-corp path are later.
- Payroll is recorded from the provider's report, not processed. The reports are accrual basis; the
  cash-basis view from decision D2 is not built yet.
- A PDF of more than 12 pages must be split before extraction.
