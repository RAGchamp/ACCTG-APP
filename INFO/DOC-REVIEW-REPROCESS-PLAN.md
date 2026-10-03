# Review Input Documents — Feedback & Re-process Plan

**Date:** 2026-10-02
**App:** `C:\Daya\RAGchamp\SMALL-BIZ-ACCTG\Web-App`
**Builds on:** `SMALL-BIZ-ACCTG-MVP1-PLAN.md` (principles P1–P7) and `HOW-ACCT-AGENT-WORKS.html`
**New screen:** **Review input documents** (`/doc-review`, menu label **Input docs**)
**New folder:** `Web-App\feedback\` (one JSON file per piece of feedback)

---

## 0. Goal and summary

Today an owner (or tester) can see a document's scan and its extracted JSON on the Review screen, but only for the books that are open, one document at a time. There is also no single place showing **where a document ended up**: which ledger accounts it was debited or credited to, which line of which financial statement it feeds, and which tax worksheet line it reaches. There is no way to record "this was read or booked wrong" as a file either.

The new screen lets the user:

1. Tick **Live documents** and/or **Sandbox documents**.
2. See a **table of every input document** in those books, each with a title and a check box.
3. Tick any number of documents and click **Review**.
4. For each chosen document, see one **box**:
   - **left:** the scanned copy (page images);
   - **right:** the **extracted data**, the **ledger tagging** (debit / credit lines), the **financial statement tagging** and the **tax document tagging**.
5. Under each box, give **feedback** when the interpretation is wrong, and optionally **re-process** the document.
6. Every piece of feedback is saved as `Web-App\feedback\<file-name>-<timestamp>.json`. It holds the path to the input file, the extracted data, the tagging and the user's feedback.

### Principles carried over

| # | Principle | What it means here |
|---|---|---|
| P1 | Claude proposes, code disposes | Re-processing produces a **new proposal**; the same code checks apply. |
| P2 | Nothing is posted without the owner | Re-processing **never posts**. The document goes back to the normal **Review** queue to be accepted again. |
| P3 | Every GL line traces to a page image | The tagging is computed from the posted lines; the scan is shown next to it. |
| P4 | Statements and tax are computed by code | Statement and tax tagging is a **lookup** from account metadata and `tax_rules\<year>`, never asked of Claude. |
| P6 | The GL is append-only | Re-processing a posted document **reverses** its entries; nothing is deleted or edited. |

---

## 1. Scope

### In this feature

- One read-mostly screen that works across the live books **and every sandbox at once**, without changing the app-wide open books (`data\active.json`).
- Per-document tagging: ledger, financial statements, tax worksheets.
- Feedback form + JSON file per submission.
- Re-process actions: **re-extract**, **re-propose with feedback**, **correct the extracted figures**. Each runs as a background job on the document's own books.
- Safe undo of posted documents (reversal) with dependency and closed-month checks.

### Not in this feature (later)

- Learning automatically from the feedback folder (e.g. turning repeated feedback into vendor rules or prompt changes). The JSON files are designed so this can be added later.
- Bulk re-processing of many documents in one click (MVP: one document per click, many boxes on one page).
- Editing ledger lines from this screen (use Review → Edit, or Ledger).

---

## 2. The screen, state by state

Route: `GET /doc-review`. The menu gets **Input docs** between *Review* and *Ledger*. The page title is **Review input documents**. The short menu label avoids confusion with the existing **Review** screen, which reviews *proposals*.

### 2.1 State A — choose the books

```
Review input documents
[ ] Live documents      [ ] Sandbox documents   (sandbox: [all sandboxes ▾])
```

- Two check boxes; **either or both** may be ticked. Nothing is listed until one is ticked.
- When **Sandbox documents** is ticked, a small dropdown chooses *all sandboxes* (default) or one sandbox by name, because there can be several (`data\sandbox\*`).
- Ticking or unticking reloads the table at once (`fetch`, no page reload). The choice is kept in the URL (`?books=live,sandbox&sandbox=all`) so it survives a reload and can be bookmarked.

### 2.2 State B — the document table

One row per **document** (not per file). A file holding three receipts gives three rows, because each receipt is read and booked separately.

| ☐ | Books | Title | Type | Date | Total | Status | File |
|---|---|---|---|---|---|---|---|
| ☐ | live | Receipt — Sysco Connecticut — 2026-01-01 — $1,798.77 | Receipt | 2026-01-01 | 1,798.77 | posted | 001-…sysco….pdf (p.1) |
| ☐ | jan-2026-scanned | Square report — 2026-01-03 — $2,117.41 net sales | Square report | 2026-01-03 | 2,117.41 | posted | 004-…pos-report-square.pdf |

- **Title** = `<type label> — <party> — <date> — <total>`, built from the stored document row. Missing parts are left out. Sandbox rows get an orange **sandbox name** badge.
- Header check box = **select all visible rows**. Above the table: a text filter (title / file name), type, status and month filters, and a counter **N selected**.
- Sort: books, then the file's processing order (`files.seq`), then `part`, the same order the Review queue uses.
- **Review** button at the bottom (also repeated at the top when the table is long). It is disabled until at least one row is ticked.
- Selections are identified as `<books>:<document_id>` (e.g. `live:12`, `jan-2026-scanned:4`), because ids repeat across books.

### 2.3 State C — one box per selected document

`GET /doc-review/show?d=live:12&d=jan-2026-scanned:4` (a plain GET, so the page can be reloaded or bookmarked). A **← Back to the list** link keeps the earlier selection.

```
┌─ Receipt — Sysco Connecticut — 2026-01-01 — $1,798.77   [jan-2026-scanned]  posted ─────────────┐
│ ┌──────────── left (scan) ────────────┐  ┌──────────── right (interpretation) ───────────────┐ │
│ │ page 1 image (click = full size)     │  │ ▸ Extracted data   (table view | JSON view)        │ │
│ │ [open original PDF]                  │  │   flags: none                                      │ │
│ │                                      │  │ ▸ Ledger tagging                                   │ │
│ │                                      │  │   entry #10  2026-01-01  Sysco Connecticut         │ │
│ │                                      │  │   5010 COGS - Food purchases      Dr 1,648.95     │ │
│ │                                      │  │   5030 COGS - Packaging & paper   Dr   149.82     │ │
│ │                                      │  │   2150 Purchases awaiting stmt    Cr 1,798.77     │ │
│ │                                      │  │        ⇄ settled by card statement #71, txn 1      │ │
│ │                                      │  │ ▸ Financial statements                             │ │
│ │                                      │  │ ▸ Tax documents                                    │ │
│ └──────────────────────────────────────┘  └────────────────────────────────────────────────────┘ │
│ Feedback ─────────────────────────────────────────────────────────────────────────────────────── │
│ What is wrong?  [ ] figures read wrong  [ ] wrong account / debit-credit  [ ] wrong statement line │
│                 [ ] wrong tax treatment  [ ] not a business document  [ ] other                    │
│ [ free text ......................................................................... ] (max 1000) │
│ Re-process:  (•) don't re-process, just save feedback                                             │
│              ( ) re-propose the entries using my feedback (Claude reviser)                        │
│              ( ) re-extract: read the scan again   [ ] at higher resolution (300 dpi)             │
│              ( ) I corrected the extracted figures (edit JSON below)                              │
│              [ ] use live Claude for this (shown only in a replay sandbox)                        │
│ [ Save feedback ]  → "Saved feedback\001-2026-01-01-purchase-receipt-sysco-connecticut-...json"   │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Left:** every page of the document (`documents.pages`), lazy-loaded. A CSV document shows its rows as a table instead of an image ("CSV export: imported exactly, without Claude"). Links: **open original PDF**, **open in Review** (only when the document's books are the open books, or via a "switch & open" link).
- **Right, four collapsible sections, all open by default:**
  1. **Extracted data:** a readable field table (scalar fields, then line or transaction tables) with a toggle to the raw JSON, plus the code-check flags (error / warn / info), the same as on the Review screen.
  2. **Ledger tagging:** see §3.1.
  3. **Financial statements:** see §3.2.
  4. **Tax documents:** see §3.3.
- **Status banner** when the document is not posted: *to propose / to review / needs attention* shows the **open proposal** as "proposed, not yet posted" (grey). *Rejected* shows the reason and no tagging. *Linked (support)* shows the entries of the document it is linked to, labelled "booked from document #N".
- **Performance:** at most **30** documents per Review click (a warning asks to select fewer). Page images use a new resized image route (§5) of about 1000 px wide JPEG, cached next to the PNG, instead of the 1.7 MB 200-dpi PNG.

---

## 3. How the tagging is computed (code only)

New module **`reports\tagging.py`**, a pure function `tag_document(conn, document_id) -> dict` with no Claude. It reads the document's journal lines (posted, not reversed; or the open proposal's lines, marked `"posted": false`) and maps each line through account metadata that already exists:

### 3.1 Ledger (debit / credit)

For each entry: entry id, date, memo, kind; for each line: account number and name, debit or credit, line memo and party, `ref` (e.g. `txn 3`).
For clearing accounts (2150, 1100, 1110, 1050, 2300) add the **settlement** from the `matches` table: *"settled by card statement #71, txn 1 on 2026-01-xx"*, or *"still open (N days)"*. Reversed entries are shown struck through, with the reversal.

### 3.2 Financial statements

Mapped from `accounts.type`, `subtype` and `cash_flow_class`, which `reports\statements.py` already uses, so the screen and the reports can never disagree:

| Account type / subtype | Statement › section › line |
|---|---|
| revenue (sales) | Income statement › Revenue › *account name* (adds to Gross sales, Net sales) |
| revenue (contra_revenue, 4090) | Income statement › Less: discounts, comps & refunds |
| cogs | Income statement › Cost of goods sold › *account* (5010/5020 also feed **food & beverage cost %**) |
| expense | Income statement › Operating expenses › *account* |
| asset (cash 1000/1010/1020) | Balance sheet › Current assets; **Cash flow** › counted in ending cash |
| asset (clearing / receivable / prepaid / inventory) | Balance sheet › Current assets; Cash flow › Operating › change in working capital |
| asset (fixed_asset) | Balance sheet › Fixed assets; Cash flow › **Investing** |
| liability | Balance sheet › Liabilities; Cash flow › Operating (or Financing for 2500 loans) |
| equity (capital / draws) | Statement of owner's equity › Contributions / Draws; Cash flow › Financing |
| every account | Trial balance and General ledger › *account* |

The section shows each line's **amount and its share of that statement line** for the period (e.g. "5010: $1,648.95 of $&lt;January total&gt; COGS - Food purchases, January 2026"). Each item links to `/reports?kind=…&start=…&end=…`, but only when the document's books are the open books; otherwise it is plain text.

### 3.3 Tax documents

Mapped from `accounts.tax_line` and `tax_rules\<year>\*.yaml`, the same sources `tax\federal.py` and `tax\sales_tax_ct.py` use:

| Source | Tax tagging |
|---|---|
| account with `tax_line` | **Schedule C** › line *n* (label from `federal.yaml`), tax year of the entry date. COGS accounts (`tax_line` 36) → **Part III line 36 Purchases** → line 42 → line 4. Line 24b shown "× 50% deductible". |
| revenue in `taxable_revenue_accounts` (4000, 4010, 4050) | **OS-114** (month of the entry) › Gross receipts and Taxable gross receipts at 7.35%; Schedule C line 1 |
| revenue in `marketplace_revenue_accounts` (4020) | **OS-114** › Gross receipts, and Less: marketplace facilitator sales; Schedule C line 1 |
| `deduction_accounts` (4090) | **OS-114** › Less: discounts, comps & refunds; Schedule C line 2 |
| 2200 credit | **OS-114** › Tax collected per the books (compared with tax due) |
| receipt flagged "possible CT use tax" | **OS-114** › Use tax on untaxed purchases |
| fixed asset accounts (1500/1510/1520) at or over the threshold | **Form 4562** fixed-asset register (+ monthly book depreciation) |
| outgoing check / bank payment to a vendor in `form_1099_candidates` | **1099-NEC** candidate list |
| self-employment | Indirectly via Schedule C line 31 → **Schedule SE**: stated once per document as "affects net profit by ±$x" |
| clearing / balance-sheet-only lines | "No direct tax line (balance sheet only)" |

Lines with no tax effect say so explicitly, so the user can tell "not taxed" apart from "missing".

### 3.4 Worked example (real sandbox data)

`jan-2026-scanned`, document #1, Sysco invoice 240891, $1,798.77, card 4417:

| Ledger | Financial statements | Tax |
|---|---|---|
| Dr 5010 1,648.95 | Income statement › Cost of goods sold › COGS - Food purchases; food cost % | Schedule C Part III line 36 → line 4 (2026) |
| Dr 5030 149.82 | Income statement › Cost of goods sold › COGS - Packaging & paper goods | Schedule C Part III line 36 → line 4 (2026) |
| Cr 2150 1,798.77 | Balance sheet › Liabilities › Purchases awaiting statement; **settled by card statement #71 txn 1** → nets to 0 at month end | none (balance sheet only) |

---

## 4. Feedback and re-processing

### 4.1 Saving feedback (always)

**Save feedback** writes the JSON file (§4.4) **first**, before any re-processing, so feedback is never lost even if the re-process fails. It also writes an `audit_log` row (`doc_feedback`, document id, file name). If the user wrote text and chose *re-propose*, it also writes a row in the existing `feedback` table (the Reviser path does that already). At least one category **or** some text is required.

### 4.2 Re-process options

| Option | What runs | Claude call | Notes |
|---|---|---|---|
| **Just save feedback** | nothing else | — | The default. |
| **Re-propose with my feedback** | `propose.revise(biz, doc, text)` (the existing Reviser) | `revise` | Keeps the extraction; the feedback text is the Reviser's instruction; a rule suggestion may appear on Review as today. |
| **Re-extract** (optional 300 dpi) | `extract.extract_file(biz, file_id, dpi)` then `_propose_docs` | `extract` + `propose` | Re-reads the **whole file**, so every document in that file is redone (see §4.3). The feedback text is passed to the extraction prompt as an `OWNER NOTE` (new `{{OWNER_NOTE}}` slot in `prompts\extract.txt`, empty by default). |
| **I corrected the figures** | `extract.update_extraction(biz, doc, json)` then `propose.propose` | `propose` | Uses the JSON edited in the box; the checks re-run first. |

Every option ends with the document back in the **Review** queue as *to review* or *needs attention*. **Nothing is posted** (P2). The box then shows "Re-processed: new proposal v3 is waiting on the Review screen", with a link.

### 4.3 Making a posted document re-processable (the safety rules)

A posted document's money is already in the ledger and may already be relied on by other documents. Before any re-process option, one transaction (`books\review.py: unpost_document()`, new) does the following:

1. **Closed month:** if any of the document's entries is in a closed month, **refuse**. The message says to reopen the month on *Reconcile* first, and the reopen is audit-logged as today. (Reversing into a later open month is a possible later option; not in this feature.)
2. **Dependants:** look in `matches` for other documents whose booking relied on this one (`other_document_id = this`), e.g. the card statement line that settled the Sysco receipt's 2150. If there are any, **refuse** and list them ("card statement #71 txn 1 settled this receipt; re-process it together"). Offer **Re-process together**, which un-posts the dependants first (newest first), then this document, so 2150 / 1100 / 1110 / 1050 / 2300 never go wrong in between. All of them go back to the Review queue.
3. **Reverse** every non-reversed entry of the document (`post.reverse_entry`, on the original date, memo "Reversal for re-processing: feedback <file>"). Delete this document's own rows in `matches` (the links it created) so `match.hints` sees the open items again.
4. Set the document to *extracted* (re-extract / corrected figures) or keep the extraction and supersede the accepted proposal (re-propose).
5. **Re-extract only:** `clear_documents` refuses while any document of the file is booked or matched. So un-post **every document of the file** (same rules 1–3) and show that list for confirmation first ("this file has 3 documents; all 3 will be re-read").
6. A **support** (linked) document holds no entries: just delete its support match and set it to *extracted*.
7. A **rejected** document: re-process puts it back (`review.reopen`) and then runs the chosen option.

The confirmation dialog lists exactly what will be reversed and re-queued before anything happens.

### 4.4 The feedback JSON file

- **Folder:** `C:\Daya\RAGchamp\SMALL-BIZ-ACCTG\Web-App\feedback\` (created at start-up in `config.py` as `FEEDBACK_DIR`, overridable with `SBA_FEEDBACK_DIR`). It is one flat folder for all books; each file says which books it belongs to. Add it to `.gitignore` (it can contain real business data).
- **Name:** `<input file name without extension>-<YYYYMMDD-HHMMSS>.json`, using the **local time the user clicked Save**. Example: `001-2026-01-01-purchase-receipt-sysco-connecticut-20261002-143512.json`. The file name is made safe for Windows (characters `\/:*?"<>|` → `_`). If the name already exists (two documents of one file in the same second), `-2`, `-3`… is added.
- **Content** (UTF-8, indented, amounts as strings with 2 decimals like the extraction, plus `*_cents` integers where the ledger uses them):

```json
{
  "schema_version": 1,
  "saved_at": "2026-10-02T14:35:12",
  "books": {"name": "jan-2026-scanned", "kind": "sandbox", "ai_mode": "replay"},
  "input_file": {
    "filename": "001-2026-01-01-purchase-receipt-sysco-connecticut.pdf",
    "path": "C:\\Daya\\RAGchamp\\SMALL-BIZ-ACCTG\\Web-App\\data\\sandbox\\jan-2026-scanned\\documents\\2026\\3d4ec15f457f2f8a\\original.pdf",
    "source_path": "C:\\Daya\\RAGchamp\\SMALL-BIZ-ACCTG\\Web-App\\data\\sandbox\\jan-2026-scanned\\inputs\\001-2026-01-01-purchase-receipt-sysco-connecticut.pdf",
    "sha256": "3d4ec15f457f2f8a…",
    "kind": "pdf", "scanned": true,
    "pages": [1],
    "page_images": ["…\\3d4ec15f457f2f8a\\page-001.png"]
  },
  "document": {"id": 1, "part": 1, "doc_type": "purchase_receipt", "status": "posted",
               "party": "Sysco Connecticut", "date": "2026-01-01", "total": "1798.77"},
  "extracted_data": { "...": "the documents.extraction JSON exactly as stored" },
  "flags": [],
  "proposal": {"version": 1, "source": "replay", "confidence": "high",
               "reason": "Standard purchase receipt posting rules.", "questions": [], "match_hints": []},
  "tagging": {
    "ledger": [{"entry_id": 10, "date": "2026-01-01", "memo": "Sysco Connecticut", "posted": true,
                "lines": [{"account": "5010", "name": "COGS - Food purchases", "debit": "1648.95", "credit": "0.00"},
                          {"account": "5030", "name": "COGS - Packaging & paper goods", "debit": "149.82", "credit": "0.00"},
                          {"account": "2150", "name": "Purchases awaiting statement (card/bank)", "debit": "0.00", "credit": "1798.77",
                           "settled_by": {"document_id": 71, "ref": "txn 1"}}]}],
    "financial_statements": [{"account": "5010", "statement": "Income statement", "section": "Cost of goods sold",
                              "line": "COGS - Food purchases", "amount": "1648.95", "period": "2026-01"}],
    "tax": [{"account": "5010", "form": "Schedule C", "line": "Part III 36 → 4", "label": "Purchases",
             "amount": "1648.95", "tax_year": 2026}]
  },
  "feedback": {
    "categories": ["wrong_account"],
    "text": "The cups are packaging; fine. But the produce line includes $40 of cleaning supplies → 6310.",
    "corrected_extraction": null,
    "reprocess": {"action": "repropose", "higher_dpi": false, "live_claude": false}
  },
  "reprocess_result": null
}
```

- `input_file.path` = the stored original the app read. `source_path` = where it came from when known: the answer key `path` for sandboxes, or `Inbox\processed\<name>` for inbox files. (Uploads have no source path; that is recorded as `null`.)
- `tagging` is the **before** state (what the user was looking at when they wrote the feedback).
- `reprocess_result` is filled in **in the same file** when the background job ends: `{"finished_at", "ok", "error", "reversed_entries": [...], "requeued_documents": [...], "new_proposal": {version, entries, confidence, errors}}`. The before/after pair is what later analysis needs.
- Category values: `figures_wrong`, `wrong_account`, `wrong_statement_line`, `wrong_tax`, `not_business`, `other`.

### 4.5 Replay sandboxes

In a sandbox in **replay** mode, Claude's answers come from `answer_key.json`. Re-extract would return the same JSON, and Re-propose works only where the key has a revised reply. So in a replay sandbox the box shows a **Use live Claude for this** check box (ticked by default when a re-process option is chosen), with the note "costs usage". The job then runs with `AI(biz, mode="live")` for that one document. The sandbox's mode is not changed. If it is left unticked, the replay result is shown with a warning that nothing can change.

---

## 5. Implementation

### 5.1 New and changed files

| File | Change |
|---|---|
| `config.py` | `FEEDBACK_DIR`, `DOC_REVIEW_MAX = 30`, `FEEDBACK_TEXT_MAX = 1000`; create the folder. |
| `reports\tagging.py` (new) | `tag_document(conn, document_id)` → ledger / statements / tax tagging (§3); uses `coa.account_map`, `rules_loader`, `matches`. |
| `books\feedback_files.py` (new) | `feedback_path(file_row, now)`, `build_record(biz, doc_id, form)`, `write`, `update_result`. |
| `books\review.py` | `unpost_document(biz, doc_id, include_dependants)` and `dependants(conn, doc_id)` (§4.3). |
| `ingest\extract.py`, `prompts\extract.txt` | Optional `owner_note` → `{{OWNER_NOTE}}` (empty by default, so existing prompts are unchanged). |
| `web\doc_review.py` (new blueprint) | Pages and API below. Every route takes the **books name** explicitly and uses `business.get(name)`; it never relies on or changes `active.json`. |
| `templates\doc_review.html`, `templates\doc_review_show.html`, `static\doc_review.js` (new) | States A–C, the feedback form, job polling (reuses `run()` / `pollJob()` from `app.js`). |
| `templates\base.html` | Menu item **Input docs**. |
| `.gitignore` | `feedback/`. |
| `README.md`, `INFO\HOW-ACCT-AGENT-WORKS.html` | New screen section; feedback folder in the file map. |

### 5.2 Routes

| Method & path | Purpose |
|---|---|
| `GET /doc-review` | State A/B shell. |
| `GET /api/doc-review/documents?books=live,sandbox&sandbox=all` | Table rows: `{key, books, books_kind, id, title, doc_type, date, total, status, filename, pages}`. |
| `GET /doc-review/show?d=<books>:<id>&d=…` | State C (server-rendered boxes; tagging computed per document). |
| `GET /doc-review/<books>/files/<file_id>/page/<n>.jpg` | Resized page image (cached `page-NNN.view.jpg`). |
| `GET /doc-review/<books>/files/<file_id>/original` | Original PDF / CSV of any books. |
| `GET /api/doc-review/<books>/<doc_id>/impact?action=…` | What re-processing would reverse and re-queue (for the confirmation dialog). |
| `POST /api/doc-review/<books>/<doc_id>/feedback` | Body: categories, text, action, higher_dpi, live_claude, include_dependants, corrected_extraction. Writes the JSON. If an action is chosen, starts a job and returns `{file, job}`. |

All `<books>` values are validated by `business.get()` (sandbox name regex), so no path can be injected.

### 5.3 Job flow for a re-process

```
POST feedback ─► write feedback\<name>-<ts>.json   (always first)
             └─► start_job("doc_reprocess")
                   biz = business.get(books); ai = AI(biz, mode="live" if live_claude else None)
                   unpost_document(...)                 # §4.3, one transaction, audit-logged
                   repropose  -> propose.revise(biz, doc, text, ai)
                   reextract  -> extract.extract_file(biz, file_id, ai, dpi, owner_note=text) + _propose_docs
                   corrected  -> extract.update_extraction(...) + propose.propose(...)
                   feedback_files.update_result(path, result)
browser polls ─► box refreshes its status line + "open in Review" link
```

`propose.revise` gets an optional `ai` argument (today it builds its own `AI(biz)`), so live Claude can be forced in a replay sandbox.

---

## 6. Testing

| Test | Checks |
|---|---|
| `test_tagging.py` | Sysco #1 → 5010/5030 COGS + Part III 36; Square report → 4000/4010/4050 OS-114 taxable, 2200 tax collected, 1100 settled by payout; DoorDash → 4020 marketplace deduction; fixed-asset receipt → 4562; 2150 line shows `settled_by` #71. |
| `test_feedback_files.py` | Name format and Windows-safe characters; collision suffix; every required key present; `input_file.path` exists; `reprocess_result` filled after the job. |
| `test_unpost.py` | Posted receipt with a dependent card statement is refused, then works with *together*; closed month refused; reversed entries balance; 2150 open again; `match.hints` offers the hint again; re-extract of a multi-document file un-posts all. |
| `test_doc_review_web.py` | Live-only / sandbox-only / both lists; selection keys across books; >30 selected → warning; image route for a non-active sandbox; feedback POST with no action writes the file and changes nothing in the books (ledger row count unchanged). |
| Scenario run | In `jan-2026-scanned` (replay): give feedback on the Sysco receipt with *re-propose* + live Claude → card statement #71 re-queued together → accept both on Review → reports and Schedule C return to the same totals (or the corrected ones). |

All tests use temporary books and a temporary `FEEDBACK_DIR`, never the real folder.

---

## 7. Phases

1. **Read-only screen:** routes, table, boxes, `tagging.py`, image route. (Useful on its own.)
2. **Feedback files:** form, JSON writer, audit log, no re-processing.
3. **Re-process:** `unpost_document`, dependants, closed-month rule, jobs, `reprocess_result`.
4. **Replay sandboxes:** live-Claude override; the `OWNER_NOTE` extraction slot.
5. **Docs & tests:** README, HOW-ACCT-AGENT-WORKS.html section, the test files above.

---

## 8. Decisions (confirmed by the owner 2026-10-02)

All recommended options below were confirmed one by one. The "Alternative" column records what was considered and not chosen.

| # | Decision | Alternative (not chosen) |
|---|---|---|
| R1 | One row per **document**, not per file. | One row per file (the title would then list its documents). |
| R2 | Both check boxes may be ticked at the same time; sandbox rows come from **all sandboxes** unless one is picked. | Make them radio buttons; or only the open sandbox. |
| R3 | Feedback is saved even without re-processing; re-processing is a separate, explicit choice. | Always re-process when feedback is given. |
| R4 | Re-processing **never posts**; the document returns to the Review queue. | Auto-accept when the new proposal passes every check. |
| R5 | Posted documents are corrected by **reversal**; documents in a closed month are refused until the month is reopened. | Reverse into the current open month. |
| R6 | Dependants block re-processing unless *Re-process together* is chosen. | Always re-process dependants automatically. |
| R7 | Feedback files go into one flat `feedback\` folder; the books name is inside the JSON. | Sub-folders `feedback\live\`, `feedback\<sandbox>\`. |
| R8 | Timestamp format `YYYYMMDD-HHMMSS`, local time. | ISO with `T` (not allowed with `:` in Windows names). |
| R9 | Up to 30 documents per Review click. | No limit (slow with large scans). |
| R10 | Menu label **Input docs** (page title *Review input documents*). | Put it as a tab on the existing Review screen. |
| R11 | In a **replay** sandbox, *Use live Claude for this* is **ticked by default** when a re-process option is chosen (§4.5); the sandbox's own mode is unchanged. | Unticked by default; or no re-processing in replay sandboxes. |
