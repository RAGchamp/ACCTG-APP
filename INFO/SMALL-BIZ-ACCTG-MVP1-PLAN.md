# Small Business Accounting Agent — MVP 1 Plan

**Date:** 2026-09-29
**New app:** `C:\Daya\RAGchamp\SMALL-BIZ-ACCTG\WEB-APP` (to be created)
**Reuses:** `C:\Daya\RAGchamp\ANN-RPT-ANALYZER\WEB-APP` (Annual Report Foot Notes Analyzer). Its PDF ingestion, Claude vision transcription, `claude -p` client, job runner and review-page patterns.
**First customer profile:** a take-out and delivery pizza shop in **Hartford, CT**, organized as an **LLC**, filing taxes only in Connecticut (and federally).

---

## 0. Goal and summary

The owner scans every paper trail of the business: receipts, credit card statements, checks, handwritten cash slips and point-of-sale (POS) statements. The app turns them into bookkeeping:

1. **Ingest** a PDF (scanned or digital).
2. **Extract** its data with Claude and turn it into **proposed ledger entries** (a table of debit and credit lines).
3. The owner **reviews** each proposal. They either **accept** it, **edit** it, or **give feedback** in plain words ("this was a personal purchase", "split the Costco bill: $40 was cleaning supplies"), and Claude revises it.
4. Accepted entries are **posted to the General Ledger (GL)**.
5. From the GL the app produces the **Trial Balance, Balance Sheet, Income Statement, Cash Flow Statement**, the **CT sales and use tax return worksheet (OS-114)**, and **federal and Connecticut income tax worksheets**.

**The plan in one line:** reuse the analyzer's *"PDF → page images/text → Claude → structured data → human review"* engine, and put a strict, deterministic **double-entry ledger** behind it. Claude proposes the entries. Code checks, posts and computes them.

### Guiding principles

| # | Principle | Why |
|---|---|---|
| P1 | **Claude proposes, code disposes.** Claude reads documents and suggests entries. Code checks every entry (debits = credits, the accounts exist, the amounts tie to the document) before it can be accepted. | An LLM must never be the thing that makes the books balance. |
| P2 | **Nothing is posted without the owner's approval.** | The owner stays responsible for their books and taxes. |
| P3 | **Every GL line traces back to a page image.** | The same idea as the analyzer's OCR review page. It makes audits and corrections possible. |
| P4 | **The statements and tax figures are computed by code from the GL, never written by Claude.** Claude may *explain* them. | They must be reproducible to the cent. |
| P5 | **Money is stored as integer cents.** No floats. | Rounding drift. |
| P6 | **The posted GL is append-only.** A correction is a reversing entry plus a new entry. | Audit trail. |
| P7 | **Tax forms are drafts for review** (by the owner or a CPA). The MVP does not e-file. | Liability. Tax rules change every year. |

---

## 1. Scope

### In MVP 1

- One business (the pizza shop), one user (the owner), running locally on the owner's PC like the analyzer.
- Ingesting PDFs of the 5 document types the owner named, plus **bank statements** (strongly recommended, see §4.1).
- Claude extraction → proposed journal entries → review (accept / edit / feedback) → GL.
- Matching, so the same money isn't booked twice (a receipt *and* the card statement line for it; a POS settlement *and* the bank deposit for it).
- Chart of Accounts pre-loaded for a pizza shop (editable).
- Periods: months and a calendar tax year, with **month close / lock**.
- Reports: General Ledger, Trial Balance, Balance Sheet, Income Statement, Cash Flow Statement (indirect method), all as HTML, CSV and PDF/print.
- A **synthetic test data generator** (§15): realistic scanned documents and CSVs for every scenario, with the answer key, sandbox businesses and an evaluation scorecard.
- Tax worksheets:
  - **CT sales and use tax (Form OS-114)**, for each filing period.
  - **Federal:** Schedule C and Schedule SE (single-member LLC) **or** Form 1065 with Schedules K-1 (multi-member LLC), plus Form 4562 (depreciation) figures.
  - **Connecticut:** the CT-1040 figures that flow from the business (single-member), or CT-1065/CT-1120SI (multi-member), plus a checklist of CT filings (annual report, estimated payments).

### Not in MVP 1 (later)

- Payroll processing (W-2, 941, CT-941, withholding). The MVP only **records** payroll from a payroll provider's report.
- Bank feeds or APIs (Plaid, Square/Toast APIs). The MVP works from PDFs, with an optional CSV import (§4.1).
- E-filing, 1099-NEC issuance, multi-user access, multi-business, cloud hosting, mobile capture app.
- Inventory costing beyond "purchases = cost of goods sold (COGS)" with a year-end count adjustment.

---

## 2. What we reuse from the Annual Report Analyzer

Inspected 2026-09-29. The analyzer is a Flask app (Python 3.12, PyMuPDF, `claude -p` through the local Claude Code login, SQLite packages, pytest with Claude mocked).

| Analyzer piece | What it does there | Reuse in the accounting app |
|---|---|---|
| `claude_client.py` | `run_claude()` sends a prompt on stdin to `claude -p --tools ""`, with streaming progress, timeouts and readable errors. `run_claude_with_image()` lets Claude read page images (Read tool only) and returns cost and time. `fill_prompt()` fills `{{KEY}}` placeholders in `prompts\*.txt`. | **Copy as-is.** It is the only way the app talks to Claude. Page images of receipts go through `run_claude_with_image`. |
| `ingest\ocr_transcribe.py` | Renders each page to a greyscale PNG (150 dpi; retries at 200 dpi when many `[?]`), asks Claude for a **JSON header + Markdown body**, parses it (`parse_reply`), caches per page (`page-007.png/.md/.json` + `manifest.json`), runs 4 pages in parallel, is resumable and cancellable, tracks cost per page. | **Adapt** into `ingest\extract.py`. Same render / ask / parse / cache / parallel / retry loop. The prompt asks for a **document-type-specific JSON** (§5) instead of an annual-report page. Handwritten cash slips may need 200–300 dpi. |
| `ingest\ocr_transcribe.is_scanned`, `ingest\pdf_utils.open_pdf` | Detects image-only PDFs, opens PDFs safely (password-protected ones rejected). | **Copy.** Digital PDFs (a downloaded card statement) send their **text layer** to Claude, which is cheaper and more exact. Scans send the image. |
| `ingest\ocr_quality.py` | `parse_amount()` ("(332,319)" → −332319, "$ 46,002.41"), `tables()` (Markdown tables → rows), `tie_outs()` (does the total row equal the sum above?), optional Tesseract cross-check of figures. | **Copy and extend.** Receipt checks: *items + tax + tip = total*. Statement checks: *opening balance + transactions = closing balance*. The Tesseract cross-check stays optional. |
| `rptpkg\store.py` | Keys each PDF by its **SHA-256**, so a renamed copy is recognised. | **Reuse** for **duplicate document detection**: the same scan uploaded twice is refused. |
| `webcommon.py` | `start_job` / `update_job` / `job_cancelled` background jobs that the browser polls; `UserError`; safe Markdown rendering. | **Copy as-is.** Extraction and proposal runs are jobs. |
| `templates\ocr_review.html` | The page image next to its transcription; unreadable characters in red, unconfirmed figures in yellow; "Re-transcribe this page". | **Pattern for the Review screen** (§6): the document image on the left, the extracted data and the proposed entries on the right. |
| `ingest\feedback.py` + `Model-Feedback\feedback-status.json` | User feedback stored as JSON with a status history (Reported → Fixed). | **Pattern for correction feedback** (§6.3): every piece of feedback is kept with its effect, and becomes a reusable **rule**. |
| `analyzer\history.py`, `Prompt-History\`, `claude-prompt-input.txt` | Every prompt and reply saved. | **Copy.** Every extraction and proposal keeps its exact prompt and reply (audit trail, P3). |
| `config.py`, `prompts\` folder, `logs\app.log`, `CLAUDE_EFFORT` / `CLAUDE_MODEL` env settings | Central settings; prompts editable without code changes. | **Copy the pattern.** |
| `tests\` with Claude mocked, `prompt_baseline.py --freeze` | Byte-identical prompt tests at no cost. | **Copy the approach.** Golden sample documents with recorded Claude replies. |

**Not reused:** notes indexing, statement finding, model documents and fingerprints, narrative sections, the Analyzer screens. They are specific to annual reports.

**How to reuse:** copy the files into the new app (not a shared package) for MVP 1. Both apps change quickly, and a shared library can come later once the common code settles. Record the source file and date at the top of each copied file.

---

## 3. Architecture

```
            ┌──────────────── Flask app (local, http://127.0.0.1:5050) ───────────────┐
 Scans ───► │ 1 INGEST        2 EXTRACT            3 PROPOSE          4 REVIEW        │
 (PDF)      │ inbox / upload  page images or text  Claude: JSON  ──►  owner accepts / │
            │ SHA-256 dedupe  → Claude (per doc    → journal entries  edits / gives   │
            │ split & classify  type schema)       + code checks      feedback ──┐    │
            │                 → code checks                            ▲         │    │
            │                   (ties, totals)                         └─revise──┘    │
            │                                                                │        │
            │ 5 GENERAL LEDGER (SQLite, append-only) ◄───── post ────────────┘        │
            │        │                                                                │
            │        ├─► 6 MATCH & RECONCILE (receipt ↔ card line, POS ↔ deposit)     │
            │        ├─► 7 REPORTS: TB, Balance Sheet, Income Stmt, Cash Flow         │
            │        └─► 8 TAX: OS-114, Schedule C/SE or 1065/K-1, CT-1040 / CT-1065  │
            └─────────────────────────────────────────────────────────────────────────┘
                      claude_client.py  (claude -p, local Claude Code login)
```

The **"agentic"** part in MVP 1 is a controlled pipeline of Claude steps, each with a clear input, a JSON schema output and a code check:

| Agent step | Input | Output | Code check |
|---|---|---|---|
| **Classifier** | page images/text of an upload | document boundaries + type per document (§4) | each page is assigned to exactly one document |
| **Extractor** | one document | typed JSON (§5) | totals tie; dates are in range; required fields present |
| **Bookkeeper** | extracted JSON + Chart of Accounts + vendor rules + similar past entries | proposed journal entries with a confidence and a reason for each | balanced; accounts exist and are active; amounts equal the document |
| **Reviser** | a proposal + the owner's feedback | a revised proposal + an optional **rule** to remember | same as Bookkeeper |
| **Matcher** (mostly code) | new card, bank or POS lines vs open documents | suggested matches | amount and date windows; Claude only breaks ties by description |
| **Questioner** | anything ambiguous | a question for the owner ("Was this Costco purchase for the business?") | shown in the review queue, never guessed |
| **Explainer** (optional) | a finished report | a plain-English summary (for example, why profit fell this month) | none needed: read-only |

A later version can move to the Claude Agent SDK or the Anthropic API (tool use, structured outputs). MVP 1 keeps the proven `claude -p` path so no API key or new billing is needed.

---

## 4. Document types and how each is booked

### 4.1 The document types

| Type | Examples | Typical content | Role in the books |
|---|---|---|---|
| `purchase_receipt` | Restaurant Depot, Costco, Sysco invoice, a gas station receipt | vendor, date, items, subtotal, sales tax, total, payment method (card last 4 / cash / check) | **Supporting document.** If paid by card or check, it is *matched* to the statement line and used to categorize or split it. If paid **cash**, it creates the entry itself (Cr Cash on hand). |
| `credit_card_statement` | the business card's monthly statement | period, opening and closing balance, a list of transactions, payments, interest and fees | **Posting source** for card purchases (Dr expense / Cr Credit card payable), fees and interest. The payment to the card is matched to the bank. |
| `check` | an image of a written check (outgoing) or a received check (incoming) | payee/payer, date, amount, memo, check number | Outgoing: Dr expense or payable / Cr Bank (or matched to the bank statement line). Incoming: Dr Undeposited funds / Cr revenue or receivable. |
| `cash_log` | handwritten daily cash sheets, petty cash slips, a cash-paid tip-out note | date, amounts, short descriptions, often messy | Cash sales not in the POS, petty cash expenses, cash drops to the bank. **Low-confidence extraction by default:** every figure shown for confirmation. |
| `pos_statement` | daily Z-report or monthly settlement report from the POS (Square / Toast / Clover), **delivery platform statements** (DoorDash, Uber Eats, Grubhub) | gross sales, discounts, refunds, sales tax collected, tips, payment mix (card / cash / platform), processor fees, net deposit | **Posting source for revenue.** Dr Cash on hand / Dr Card clearing / Dr Platform receivable, Cr Sales (food), Cr Sales tax payable, Cr Tips payable. Fees are booked when the payout arrives. |
| `bank_statement` *(added)* | the business checking account's monthly statement | period, opening and closing balance, deposits, withdrawals, checks cleared | **The anchor for reconciliation** and for the Cash Flow Statement. Without it, cash cannot be proven. Also the source for rent, utilities, loan payments and owner draws paid by transfer. |
| `other` | a loan statement, an equipment invoice, an insurance bill, a payroll provider report | varies | Extracted generically. The Bookkeeper proposes; flagged for close review. |

**Optional CSV import** (card, bank or POS exports) uses the same pipeline from step 3. It is cheap and exact, and most POS systems and banks offer it. It is recommended alongside the PDFs.

### 4.2 Avoiding double counting (the key bookkeeping rule)

One purchase can appear on **three** documents: the receipt, the card statement and the bank payment of the card. Posting rules:

| Money moved by | Posting source | Supporting documents (matched, not posted) |
|---|---|---|
| Credit card | card statement line | receipt |
| Debit card / ACH / check | bank statement line | receipt, check image |
| Cash | receipt or cash log | — |
| Customer card sales | POS settlement (sales side) | bank deposit (clears *Card clearing*) |
| Delivery platform sales | platform statement | bank deposit (clears *Platform receivable*) |

When a receipt arrives **before** its statement, it is booked to a clearing account (*Card purchases awaiting statement*). The statement line then matches it and clears it, instead of creating a second expense. The Matcher (§7) does this. Unmatched items older than 45 days appear on the review dashboard.

### 4.3 Pizza-shop specifics the Bookkeeper must know (in its system prompt)

- **Tips** paid by card are **not revenue**. They are a liability (*Tips payable*) until paid to staff.
- **Sales tax collected** is **not revenue**. It is a liability (*Sales tax payable*) until remitted with OS-114.
- **Marketplace facilitators** (DoorDash, Uber Eats, Grubhub) generally collect and remit CT sales tax on the orders they process. Those sales must not be taxed again on the shop's return. How they are reported on OS-114 must be confirmed (§10.2).
- **Delivery fees** charged by the shop, **discounts / coupons**, **refunds / voids** and **comps** each get their own account, so gross versus net sales is visible.
- **Platform commissions** and **card processing fees** are expenses, netted out of deposits. The entry must gross them up (sales at gross, fees as an expense).
- **Food and beverage purchases** → COGS; **paper goods / boxes** → COGS-Packaging; **cleaning supplies** → Supplies (expense).
- **Equipment** (ovens, mixers, a delivery vehicle) → Fixed assets when over the capitalization threshold (default $2,500 per item: the IRS de minimis safe harbor for businesses without audited statements; to be confirmed).
- **Owner's personal spending** through the business → *Owner draws* (equity), never an expense. A single-member LLC owner is not paid wages.
- **Vehicle / delivery mileage** paid to drivers, and the shop's own vehicle costs, are tracked separately (they feed Schedule C Part IV / Form 4562).

---

## 5. Extraction (steps 1–2)

### 5.1 Ingest

- Two ways in: drop PDFs into `SMALL-BIZ-ACCTG\Inbox\` and click ↻ (like the analyzer's reports folder), or upload in the browser.
- Each PDF is hashed (SHA-256, `rptpkg\store.py` pattern). A PDF seen before is refused, with a link to the earlier one.
- Stored under `data\documents\<yyyy>\<sha16>\` (original PDF, page PNGs, per-page JSON, prompts and replies).
- **A multi-document PDF** (the owner scans 12 receipts in one batch) is split by the **Classifier**: one Claude call over small thumbnails of all the pages returns document boundaries and types. The owner can fix the split on the review screen.

### 5.2 Extract

- **Text-layer PDFs** (downloaded statements): the page text (PyMuPDF, with the analyzer's table-row rebuilding where it helps) goes to `run_claude()`.
- **Scanned PDFs**: page images go to `run_claude_with_image()`, 150 dpi, **200–300 dpi for `cash_log` and faint thermal receipts**, grey or colour (thermal receipts often scan better in colour, to be tested).
- One prompt per document type (`prompts\extract_<type>.txt`), each asking for a fenced ```json block that matches a schema (`schemas\<type>.json`), for example:

```json
{
  "doc_type": "purchase_receipt",
  "vendor": "Restaurant Depot",
  "vendor_address": "…",
  "date": "2026-09-14",
  "currency": "USD",
  "lines": [
    {"description": "Mozzarella 6x5lb", "qty": 2, "amount": "84.98", "taxable": false},
    {"description": "Pizza boxes 16in x50", "qty": 1, "amount": "32.50", "taxable": true}
  ],
  "subtotal": "117.48",
  "tax": "2.06",
  "tip": null,
  "total": "119.54",
  "payment": {"method": "card", "card_last4": "4417", "check_no": null},
  "unreadable": 0,
  "field_confidence": {"total": "high", "date": "high", "lines": "medium"}
}
```

- The rules from `prompts\transcribe_page.txt` carry over: *copy figures exactly as printed, never correct or recompute, mark unreadable characters `[?]`, don't guess.* Amounts come back as **strings**; code parses them with `parse_amount()` into cents.
- A statement with many pages is extracted page by page (in parallel), then joined. Code checks that transaction lists don't repeat at page boundaries.

### 5.3 Code checks after extraction (no Claude)

| Document | Check | If it fails |
|---|---|---|
| Receipt | Σ lines = subtotal; subtotal + tax + tip = total | Yellow flag on the figures; the owner confirms or re-extracts at higher dpi |
| Receipt | tax / taxable subtotal ≈ a CT rate (6.35%, or 7.35% for meals) | Flag "unusual tax rate" |
| Card / bank statement | opening + Σ transactions = closing | Red flag. Usually a missed or doubled line. Re-extract the page |
| Card / bank statement | the opening balance = the previous month's closing balance in the app | Flag "gap in statements" |
| POS | gross − discounts − refunds = net; tax ≈ 7.35% of taxable sales; the payment mix adds to the total | Flag |
| Any | date inside an open period, not in the future | Refuse to post into a locked period |
| Any | `[?]` present | The figure must be confirmed by the owner |

The **optional Tesseract cross-check** from the analyzer (`figure_cross_check`) is kept for scans: figures that neither Tesseract nor a tie-out confirms are shown in yellow.

---

## 6. Proposals, review and feedback (steps 3–4)

### 6.1 The Bookkeeper prompt

Sent to Claude (text only, `--tools ""`):

1. **BUSINESS PROFILE**: the pizza shop, Hartford CT, LLC type, accounting basis, the capitalization threshold, the shop's card last 4 digits and bank account last 4, the sales tax rates.
2. **CHART OF ACCOUNTS**: number, name, type, and a one-line "use for" description.
3. **VENDOR RULES**: learned rules for this vendor (§6.3), for example "Restaurant Depot → 5010 Food purchases, except paper goods → 5030".
4. **SIMILAR PAST ENTRIES**: up to 5 accepted entries for the same vendor or document type (retrieved by vendor name and type, no vector DB needed in MVP 1).
5. **THE DOCUMENT**: the checked extraction JSON and its flags.
6. **INSTRUCTIONS**: return a JSON list of journal entries, each with a date, memo, lines `{account, debit, credit, memo}`, a `confidence` (high/medium/low), a `reason` in one sentence, and `questions` for the owner when something can't be decided.

**Code then checks** the proposal: each entry balances to the cent, the accounts exist, the total equals the document total, the date is in an open period, and the tax/tips lines match the extracted tax/tips. A proposal that fails is sent back to Claude once with the error; if it fails again, it goes to review marked "needs manual entry".

### 6.2 The Review screen (`/review`)

Built on the pattern of `ocr_review.html`:

```
┌─ Review queue (left) ──┐ ┌─ Document ──────────────┐ ┌─ Proposed entry ──────────────────────────┐
│ ● 14 to review          │ │  [page image, zoom,     │ │ Extracted: Restaurant Depot  2026-09-14    │
│ ○ 3 questions           │ │   page 1 of 1]          │ │ total $119.54  (card …4417)  ✔ ties        │
│ ○ 6 unmatched (45 d+)   │ │                         │ │                                            │
│ filters: type, vendor,  │ │  figures highlighted    │ │ Date  Account              Debit   Credit  │
│ month, confidence       │ │  where they were read   │ │ 9/14  5010 Food purchases  84.98           │
│                         │ │                         │ │ 9/14  5030 Packaging       34.56           │
│                         │ │                         │ │ 9/14  2150 Card awaiting stmt       119.54 │
│                         │ │                         │ │ Reason: RD is a food vendor; boxes → 5030  │
│                         │ │                         │ │ Confidence: high                           │
│                         │ │                         │ │ [Accept] [Edit] [Feedback…] [Reject] [Skip]│
└─────────────────────────┘ └─────────────────────────┘ └────────────────────────────────────────────┘
```

- **Accept** posts it to the GL (§8).
- **Edit** makes the table editable (account picker, amounts). The balance and the tie to the document are checked live. Posting is blocked until the entry balances.
- **Feedback…** is a text box (max 400 characters, as in the analyzer). It starts a **Reviser** job that returns a new proposal and, where the feedback is general, a proposed **rule** ("Remember: always book Restaurant Depot paper goods to 5030?" [Yes] [Only this time]).
- **Reject** marks the document "not business" / "duplicate" / "unreadable", with a reason. Nothing is posted.
- **Bulk accept** for high-confidence proposals that passed every check (for example, a card statement with 60 lines, grouped by suggested account). The owner can untick lines first.
- Keyboard shortcuts (A accept, E edit, F feedback, J/K next/previous), for speed through a month of receipts.

### 6.3 Learning from feedback

Each piece of feedback is stored (`feedback` table) with the document, the proposal before and after, and its status. Accepted **rules** go into the `vendor_rules` table and are sent with every later proposal for that vendor (§6.1 item 3). A rules page lists them for editing and deleting. The acceptance rate per vendor and document type is tracked, so bulk accept can be offered only where it has earned trust.

---

## 7. Matching and reconciliation

- **Receipt ↔ card or bank line:** same amount (±$0.01, or ±20% for a restaurant tip difference), date within −3/+7 days, card last 4 when present. Several candidates → Claude compares descriptions ("RSTRNT DEPOT #312 HARTFORD" ↔ "Restaurant Depot"). The owner confirms matches below high confidence.
- **POS settlement ↔ bank deposit:** batch net amount, deposited 1–3 business days later. The difference = processor fees (booked on the proposal).
- **Platform statement ↔ bank deposit:** payout amount. Commissions and platform fees are booked from the statement.
- **Card statement payment ↔ bank withdrawal:** clears *Credit card payable*.
- **Bank reconciliation screen** per month: the statement closing balance vs the GL cash balance, with the unmatched items on each side. A month can be **closed (locked)** only when bank and card reconcile, or with a written override.

---

## 8. General Ledger and data model

SQLite (one file, `data\books.db`), like the analyzer's packages. Money in **integer cents**.

| Table | Key columns |
|---|---|
| `business` | name, EIN, address, entity type (SMLLC / multi-member LLC / S-corp election), tax year, accounting basis, sales tax filing frequency, CT registration number |
| `accounts` | number, name, type (asset / liability / equity / revenue / COGS / expense), subtype, `tax_line` (map to Schedule C / 1065 line), `cash_flow_class`, active |
| `documents` | sha256, file path, type, source (upload / inbox / CSV), status (new → extracted → proposed → reviewed → posted / rejected), period |
| `pages` | document, page no, image path, text, extraction JSON, flags, cost, seconds |
| `extractions` | document, schema version, JSON, checks result, prompt/reply file paths |
| `proposals` | document, version, JSON entries, confidence, reason, questions, status (open / superseded / accepted / rejected) |
| `journal_entries` | id, date, period, memo, source document, proposal, posted_at, posted_by, `reverses` (id), locked |
| `journal_lines` | entry, account, debit_cents, credit_cents, memo, vendor/customer, match_id |
| `vendors` | name, aliases (statement spellings), default account, 1099 flag |
| `vendor_rules` | vendor, rule text, structured hint (account, split pattern), created from feedback id |
| `feedback` | document, proposal before/after, text, rule proposed/accepted, timestamp |
| `matches` | posting line ↔ supporting document/line, method, confidence, confirmed |
| `periods` | month, status (open / closed), closed_at, reconciliation summary |
| `tax_returns` | form, period, figures JSON, rules version, generated_at, status (draft / reviewed / filed) |
| `audit_log` | every change: who, when, what, before/after |

Invariants enforced in code **and** tests: every entry balances; posted entries are never updated or deleted (only reversed); no posting into a closed period; the Trial Balance always nets to zero.

### 8.1 Starter Chart of Accounts (pizza shop, editable)

| No. | Account | No. | Account |
|---|---|---|---|
| 1000 | Cash on hand (register) | 4000 | Food & beverage sales – in store / take-out |
| 1010 | Business checking | 4010 | Food & beverage sales – delivery (own drivers) |
| 1020 | Petty cash | 4020 | Food & beverage sales – delivery platforms |
| 1050 | Undeposited funds | 4050 | Delivery fee income |
| 1100 | Card sales clearing (POS) | 4090 | Discounts, comps & refunds (contra-revenue) |
| 1110 | Delivery platform receivable | 5010 | COGS – Food purchases |
| 1200 | Inventory (year-end count) | 5020 | COGS – Beverages |
| 1300 | Prepaid expenses | 5030 | COGS – Packaging & paper goods |
| 1500 | Kitchen equipment | 6000 | Wages (from payroll reports) |
| 1510 | Furniture & fixtures | 6010 | Payroll taxes |
| 1520 | Vehicles | 6100 | Rent |
| 1590 | Accumulated depreciation | 6110 | Utilities (gas, electric, water) |
| 2000 | Accounts payable | 6120 | Telephone & internet |
| 2100 | Business credit card payable | 6200 | Card processing fees |
| 2150 | Card purchases awaiting statement | 6210 | Delivery platform commissions & fees |
| 2200 | CT sales tax payable | 6300 | Repairs & maintenance |
| 2210 | Tips payable | 6310 | Cleaning & kitchen supplies |
| 2300 | Payroll liabilities | 6400 | Vehicle & delivery expense |
| 2500 | Loans payable (equipment / SBA) | 6410 | Advertising & marketing |
| 3000 | Owner's (members') capital | 6500 | Insurance |
| 3100 | Owner's (members') draws | 6510 | Licenses, permits & fees |
| 3200 | Opening balance equity | 6520 | Professional fees (accountant, legal) |
| 3900 | Retained earnings (system) | 6600 | Depreciation expense |
|  |  | 6700 | Bank fees & interest |
|  |  | 6900 | Miscellaneous (requires a memo) |

Each account carries its `tax_line` (for example 6100 → Schedule C line 20b "Rent – other business property"; 1065 line 13) and its `cash_flow_class` (operating / investing / financing / cash), so the reports and tax worksheets are pure lookups.

---

## 9. Financial statements (step 7)

All computed by code from posted GL lines for a chosen period (month, quarter, year to date, year) with comparatives (prior period, prior year):

| Report | How |
|---|---|
| **General Ledger** | every line by account, with running balances; each line links to its document image |
| **Trial Balance** | account balances; must net to zero (checked on screen) |
| **Income Statement** | Revenue (gross, less discounts/refunds) → COGS → **Gross profit** (and food-cost % — a key pizza-shop KPI, typically watched at 25–35%) → Operating expenses → **Net income** |
| **Balance Sheet** | Assets = Liabilities + Equity, with the current-year net income in equity. Checked to balance |
| **Cash Flow Statement** | **Indirect method:** net income, + depreciation, ± changes in working capital accounts (by `cash_flow_class`), investing (equipment), financing (loans, owner contributions and draws). The ending cash must equal the GL cash accounts (checked) |
| **Owner's equity statement** | opening capital + contributions + net income − draws |

Output: HTML (Bootstrap, print-ready, like the analyzer's formatted report), CSV, and PDF via the browser's print. The **Explainer** (optional) adds a short plain-English commentary (for example, "food cost rose from 29% to 33% because cheese purchases rose 22%"), clearly labelled as Claude's comment.

**Opening balances:** a setup wizard for the first period (bank and card balances, equipment at cost and accumulated depreciation, loans, the owner's capital). Balanced against *Opening balance equity*.

---

## 10. Tax worksheets (step 8)

### 10.1 Design

- Tax rules live in **versioned data files**, not code: `tax_rules\<year>\federal.yaml`, `ct.yaml`, `sales_tax.yaml` (rates, thresholds, form line numbers, due dates, account → line mappings). A new tax year = a new folder, reviewed against that year's forms.
- Each worksheet shows **every line with its figure and the accounts it came from** (click through to the GL lines). Missing inputs are listed as questions (for example, "home office? vehicle business-use %? health insurance?").
- Output: an HTML worksheet laid out like the form, with line numbers. **Phase 6+:** fill the official fillable IRS/CT PDFs (pypdf) as a draft to print. Every page is watermarked **"DRAFT – prepared from your books, review before filing"**.
- The tax year's rules version is stored with each generated worksheet (`tax_returns.rules_version`).

### 10.2 Connecticut sales and use tax (filed by the shop)

| Item | MVP handling (verify each year against CT DRS guidance) |
|---|---|
| Rate | **7.35%** on meals (6.35% general rate + 1% meals tax, in effect since Oct 1, 2019). 6.35% on other taxable sales. Hartford has no local sales tax (CT has no local sales taxes) |
| Form | **OS-114**, Sales and Use Tax Return, filed and paid on **myconneCT** |
| Frequency | monthly, quarterly or annual, as assigned by DRS based on the shop's liability; stored in `business` |
| Worksheet | gross receipts → deductions (sales for resale, marketplace facilitator sales, other exempt sales) → taxable gross receipts → tax due at each rate → **compared with CT sales tax payable (2200) in the GL**; any difference is flagged (rounding, uncollected tax, errors) |
| Marketplace facilitators | sales via DoorDash / Uber Eats / Grubhub: the facilitator collects and remits. **Confirm with DRS/a CPA how they must appear on OS-114** before the first return |
| Use tax | taxable purchases where no CT tax was charged (for example, an out-of-state online equipment order) — flagged from receipts with $0 tax |
| Payment entry | the remittance is booked Dr 2200 / Cr 1010 when the bank line appears |

### 10.3 Federal income tax (depends on the LLC's tax classification — decision D1)

| LLC classification | Federal return | What the app produces |
|---|---|---|
| **Single-member LLC** (default: disregarded entity) | Owner's **Form 1040** with **Schedule C** (Profit or Loss from Business) and **Schedule SE** (self-employment tax) | Schedule C worksheet: Part I income (gross receipts, returns & allowances, COGS from Part III), Part II expenses by line (advertising, car & truck, commissions & fees, depreciation, insurance, interest, legal & professional, rent, repairs, supplies, taxes & licenses, utilities, wages, other), Part III COGS (opening inventory + purchases − closing inventory), Part IV vehicle info; net profit → SE figures. Quarterly **1040-ES** estimate helper |
| **Multi-member LLC** (default: partnership) | **Form 1065** + **Schedule K-1** for each member | Form 1065 page 1 (income, deductions), Schedule B questions checklist, Schedule K, Schedules L (balance sheet from the GL), M-1, M-2, and K-1 allocations by ownership % |
| **LLC with an S-corp election** (Form 2553) | **Form 1120-S** + K-1s | Out of MVP 1 (needs owner wages via payroll). Noted for later |

Also in all cases: **Form 4562** figures (depreciation, Section 179 / bonus depreciation choices entered by the owner or CPA; the app keeps a fixed-asset register with cost, date placed in service, method and life), and a **1099-NEC** list (vendors marked 1099, paid ≥ the threshold, not by card).

### 10.4 Connecticut income tax

| LLC classification | CT filing | What the app produces |
|---|---|---|
| Single-member LLC | Owner's **CT-1040** (the Schedule C profit flows through federal AGI) | the business figures that flow in, and a **CT-1040ES** estimate helper |
| Multi-member LLC (or S-corp) | **CT-1065/CT-1120SI** (the pass-through entity return; the pass-through entity tax became **elective** for tax years from 2024 — confirm the current rules) and CT Schedule K-1s | worksheet from the 1065 figures |

**CT compliance checklist** (reminders, not forms): LLC **annual report** to the CT Secretary of the State; sales tax permit renewal; **business personal property declaration** to the **Hartford assessor** (the Oct 1 grand list; the equipment register provides the list); estimated tax due dates; CT-941/withholding if there are employees (out of MVP). Each shown with its due date on a **tax calendar** page. All dates and amounts go in `tax_rules\<year>\ct.yaml` and are verified yearly.

---

## 11. Screens

| Screen | Purpose |
|---|---|
| **Dashboard** `/` | documents to review, questions, unmatched items, this month's sales / food cost % / net income, bank balance vs GL, next tax due dates |
| **Inbox / Upload** `/inbox` | upload or refresh the inbox folder; per document: type, status, cost of extraction; **Re-extract** (higher dpi) |
| **Review** `/review` | §6.2 |
| **General Ledger / Journal** `/ledger` | search and filter entries, open the source image, **Reverse** an entry, manual journal entry (for example, depreciation, year-end inventory) |
| **Reconcile** `/reconcile` | §7, per account per month; **Close month** |
| **Reports** `/reports` | §9 |
| **Taxes** `/taxes` | OS-114, Schedule C/SE or 1065/K-1, CT, Form 4562 figures, calendar, questions |
| **Test data** `/test-data` | §15: generate scenarios or a simulated period into a sandbox, run the evaluation, view scorecards and answer keys |
| **Setup** `/setup` | business profile, LLC classification, Chart of Accounts, opening balances, vendor rules, card/bank last 4 digits, sales tax frequency |

Same stack as the analyzer: Flask + Jinja templates + small vanilla JS files + Bootstrap, background jobs polled by the browser.

---

## 12. Proposed folder layout

```
SMALL-BIZ-ACCTG\
  INFO\                        plans (this file)
  Inbox\                       drop scanned PDFs here
  WEB-APP\
    app.py                     Flask app, blueprints
    config.py                  paths, Claude settings (copied pattern), business defaults
    claude_client.py           COPIED from ANN-RPT-ANALYZER (unchanged)
    webcommon.py               COPIED (jobs, UserError, Markdown)
    ingest\
      intake.py                inbox/upload, SHA-256 dedupe (from rptpkg\store.py)
      pdf_utils.py             COPIED subset (open_pdf, is_scanned, text rows)
      pages.py                 render page images (from ocr_transcribe.render_page)
      classify.py              Classifier: split + type
      extract.py               Extractor: per-type prompt, parse, cache, parallel, retry (from ocr_transcribe)
      checks.py                ties and totals (from ocr_quality: parse_amount, tables, tie_outs)
      csv_import.py            bank / card / POS CSV
    books\
      db.py, schema.sql        the ledger database
      money.py                 cents, parsing, formatting
      coa.py                   Chart of Accounts, starter pizza-shop COA
      propose.py               Bookkeeper + Reviser prompts, proposal checks
      post.py                  posting, reversing, period locks, invariants
      match.py                 matching and reconciliation
      rules.py                 vendor rules from feedback
    reports\
      statements.py            TB, IS, BS, CF, equity
      render.py                HTML/CSV
    tax\
      rules_loader.py          tax_rules\<year>\*.yaml
      sales_tax_ct.py          OS-114 worksheet
      federal.py               Schedule C/SE, 1065/K-1, 4562 figures, 1099 list
      ct_income.py             CT-1040 / CT-1065 figures, calendar
    testdata\                  §15: scenarios\ (YAML), truth.py, layouts\, render.py, degrade.py, simulate.py, evaluate.py, fonts\, results\
    tax_rules\2025\ , 2026\    federal.yaml, ct.yaml, sales_tax.yaml
    prompts\                   classify.txt, extract_<type>.txt, bookkeeper_system.txt, bookkeeper.txt, reviser.txt, explain.txt
    schemas\                   <doc type>.json, proposal.json
    templates\, static\
    data\                      books.db, documents\ (images, JSON, prompts/replies), sandbox\<name>\  — NOT in git
    tests\                     fixtures\ (sample scans + recorded Claude replies)
```

---

## 13. Phases

| Phase | Deliverable | Done when |
|---|---|---|
| **0. Skeleton** (0.5 day) | New app; copied `claude_client.py`, `webcommon.py`, `pdf_utils` subset, `config.py`; SQLite schema; starter COA; Setup screen | `python app.py` runs; tests for money and invariants pass |
| **0b. Test data core** (2 days) | `testdata\`: scenario YAML, ledger truth, PDF/CSV rendering, scan look, sandboxes, `python -m testdata`; receipts, card, Square and bank layouts | `smoke-1-week` generates deterministically (same seed → same files) |
| **1. Ingest + extract** (2–3 days) | Inbox/upload, dedupe, page images, Classifier, Extractor for **receipt, card statement, Square POS report, bank statement** (PDF + CSV import for Square and the bank); checks; per-document cost | 20 real scanned documents **and** the Phase 1 synthetic scenarios (all difficulties) extract with all totals tied or clearly flagged; 0 silently wrong figures |
| **2. Propose + review + post** (3 days) | Bookkeeper prompt, proposal checks, Review screen (accept / edit / feedback / reject / bulk), GL posting, reversal, journal screen | `jan-2026-scanned` booked end to end (mocked and live) and matches its answer key; the Trial Balance nets to zero |
| **3. More types + matching** (2–3 days) | check, cash log (handwriting), DoorDash / Uber Eats / Grubhub statements, payroll provider report; Matcher; reconcile and close month; handwriting, check, platform and payroll layouts in the generator | bank and card reconcile for a test month; no double-counted receipt |
| **4. Financial statements** (2 days) | TB, IS, BS, CF, equity; opening balances wizard; HTML/CSV/print | statements tie (BS balances, CF ending cash = GL cash) on test data and a hand-built example |
| **5. CT sales tax** (1–2 days) | OS-114 worksheet, GL comparison, calendar | a test month matches a hand calculation to the cent |
| **6. Income tax worksheets** (3 days) | Schedule C/SE or 1065/K-1, 4562 figures, 1099 list, CT figures, fillable-PDF drafts | a full test year reviewed against the official forms (ideally by a CPA) |
| **7. Hardening** (2 days) | business simulator presets `q1-2026-messy` and `year-2026`, the Test data screen and the scorecard; learning rules, dashboard KPIs, backups, audit log view, docs (README, how-it-works HTML like the analyzer's) | README and tests complete |

About **4–5 weeks** of focused work (including the test data generator), similar in size to the analyzer.

---

## 14. Testing

- **Claude mocked** in all unit and integration tests, with recorded replies (the analyzer's approach); **prompt baseline** tests freeze each prompt.
- **Synthetic scenarios (§15)** are the main test input: every scenario in the catalog runs in mocked mode in `pytest`, and the live scorecard is run when prompts change.
- **Golden documents:** 3–5 real (redacted) samples per document type in `tests\fixtures\`, with the expected extraction JSON and expected entries.
- **Accounting invariants** (property tests): random valid postings always give a TB of zero, a balanced BS, CF ending cash = GL cash; reversals restore balances; closed periods reject postings.
- **Double-count tests:** receipt + card line + card payment → one expense only.
- **Tax tests:** hand-calculated OS-114 and Schedule C for a small synthetic year.
- **Live extraction check** (manual, costs Claude usage): a script that re-extracts the golden set and reports field accuracy, run when prompts change.

---

## 15. Synthetic test data generator

The app can **create its own realistic test documents**, the scanned PDFs and CSV exports the owner would feed it. Each document comes with the **answer key**: what should be extracted, which entries should be posted, and what the statements and tax forms should show. This lets every feature be tested without the owner's real paperwork, which is private, slow to collect, and doesn't cover the rare cases.

### 15.1 What it produces for each scenario

| Output | Example | Used to test |
|---|---|---|
| **Input documents** | `receipt-restaurant-depot-2026-01-14.pdf` (a scanned-looking thermal receipt), `square-daily-2026-01-14.pdf`, `bank-2026-01.csv` | ingest, classify, extract |
| **Expected extraction** | `…receipt….expected.json`, in the same schema as §5.2 | Extractor field accuracy |
| **Expected journal entries** | `expected_entries.json` (accounts, debits, credits) | Bookkeeper, posting rules, double-count rules |
| **Expected matches** | receipt ↔ card line, Square payout ↔ bank deposit | Matcher, reconciliation |
| **Expected results** | TB, IS, BS, CF, OS-114, Schedule C/SE, CT-1040 figures for the period | reports and tax worksheets, to the cent |
| **Recorded Claude replies** (made from the expected extraction and entries) | `replies\extract-….txt` | the free mocked test suite (§14) |
| **Owner "script"** | "accept", or "feedback: *this was personal*", or "edit: move $40 to 6310" | the Review/feedback loop, driven automatically |

### 15.2 How it works: truth first, documents second

```
 scenario (YAML) ──► 1 ledger truth ──► 2 documents ──► 3 "scan" look ──► files + answer key
   or simulation      (the real            (PDF / CSV,        (rotation, blur,      in a sandbox
   of a period        transactions,        laid out like      noise, fading,        business
                      in cents)            each source)       handwriting)
```

1. **Ledger truth.** A scenario (or a simulated month or year) is first written as the *real* business events, for example "Jan 14: bought $84.98 cheese + $32.50 boxes at Restaurant Depot, card …4417, tax $2.06". The expected entries, matches, statements and tax figures are computed **from this truth by the app's own ledger, report and tax code (§8–10)**. So the answer key is always consistent: debits = credits, and the BS balances.
2. **Documents.** Each event is rendered into the document(s) it would really create: a receipt, a line on the card statement, a line on the bank statement when the card is paid, and so on. Rendering uses **PyMuPDF** (already used; `pymupdf.Story` builds PDFs from HTML) with one HTML/CSS layout per source: thermal receipt, Sysco-style invoice, Square Sales Summary, DoorDash / Uber Eats / Grubhub payout statements, bank and card statements, a check, a payroll report. CSV files follow the Square and bank export columns.
3. **"Scan" look** (optional, per difficulty level), with **Pillow** + NumPy: pages are turned into images and back to PDF with a slight rotation, blur, noise, low contrast, faded thermal print, a crease or shadow, JPEG compression, and 150–300 dpi. **Handwriting** for cash logs and checks uses open-licence handwriting fonts (for example Caveat, Homemade Apple and Reenie Beanie from Google Fonts, OFL), with jittered letter position, size and slant, crossed-out figures, and ink-coloured strokes.

**Difficulty levels:**
- `clean`: a digital PDF with a text layer.
- `scanned`: a good scan.
- `messy`: faded, skewed, handwritten, a coffee stain.
- `adversarial`: a digit that really is unreadable, or totals that don't add up. This tests that the app *flags* the problem rather than guessing.

**Deterministic:** each run has a **seed**. The same scenario + seed + generator version gives byte-identical files, so the tests are repeatable (the analyzer's prompt-baseline idea).

**Variety (optional, uses Claude):** a `--vary` option asks Claude once for realistic variety: vendor names and addresses in the Hartford area, item lists, memo lines, and cash log notes written the way a busy owner writes them. The result is **cached in the scenario folder**, so later runs cost nothing and stay deterministic. Without `--vary`, built-in lists are used (free).

**Clearly synthetic:** every generated PDF carries `SYNTHETIC TEST DATA` in its PDF metadata and in a small footer, and uses fake names, fake card and account numbers, and a fake EIN. **Intake refuses synthetic documents in the real books**, and real documents in a sandbox.

### 15.3 Sandbox businesses

Generated data is never mixed with the real books. Each test set goes into its own **sandbox** (`data\sandbox\<name>\books.db` + documents), with the same pizza-shop setup (D1–D10, §18). A banner at the top of every screen shows which business is open ("Sandbox: jan-2026-messy" in orange). A sandbox can be reset, regenerated or deleted in one click.

### 15.4 Scenario catalog: every unique case

Scenarios live in `testdata\scenarios\*.yaml`. Each has an id, the events, the difficulty, and the expected outcome (post / flag / ask a question / refuse). The starting catalog:

| Area | Scenarios |
|---|---|
| **Purchase receipts** | paid by card (→ *awaiting statement*, then matched) · paid **cash** (→ Cr Cash on hand) · paid by check · split categories (food + boxes + cleaning) · 7.35% vs 6.35% vs no tax · tip on a restaurant receipt · a **personal item** (→ Owner draws after feedback) · equipment **≥ $2,500** (→ fixed asset) and just under it (→ expense) · a **return / refund** receipt · an out-of-state order with $0 tax (→ **use tax** flag) · several receipts on one scanned page · a multi-page Sysco invoice · a rotated or upside-down scan · a faded thermal receipt · the **same receipt uploaded twice** (refused) · a receipt that **arrives after** its card statement |
| **Credit card statement** | a month of purchases (all matched to receipts, some without receipts) · a refund credit · interest and a late fee · annual fee · the payment from the bank · a multi-page statement with a line split across pages · a **statement gap** (a missing month) · an opening balance that doesn't match last month's closing balance |
| **Bank statement (PDF + CSV)** | Square payouts (card sales less fees) · DoorDash / Uber Eats / Grubhub payouts · checks cleared (outgoing) · a customer check deposited · a cash deposit from the register · rent, utilities, insurance by ACH · a **loan payment** (principal + interest split) · an **owner draw** and an **owner contribution** · the **CT sales tax payment** · payroll provider debits · a bank fee · a **bounced (NSF) check** · a transfer to petty cash · the PDF and the CSV of the same month (no double posting) |
| **Checks** | an outgoing check to a vendor · a check to the landlord · a check received from a catering customer · a **voided** check · a check whose written amount differs from its numeric amount (flag) |
| **Handwritten cash logs** | daily cash sales not in Square · a petty cash purchase with no receipt · a cash tip-out to drivers · a cash drop to the bank · a crossed-out and rewritten figure · an **illegible digit** (must be `[?]` and asked) · a sheet with a sum error |
| **Square (POS)** | a daily close-of-day report · a monthly summary · a cash / card / gift card mix · discounts and **comps** · refunds and voids · **card tips** (→ Tips payable) · the shop's **delivery fee** (taxable, D8) · processing fees netted from the payout · a payout covering two days · the Square CSV and PDF of the same day |
| **Delivery platforms** | weekly DoorDash, Uber Eats and Grubhub statements · commissions and marketing fees · promotions paid by the shop · error charges / adjustments · **marketplace facilitator tax** shown but not owed by the shop · a payout that nets a prior-week adjustment |
| **Payroll** | a provider payroll report (wages, employer taxes, withholding) · cash tips reported through payroll · the matching bank debits |
| **Period and year-end** | opening balances on 2026-01-01 · month close after reconciliation · an attempt to post into a **closed month** (refused) · a **reversing** correction · monthly depreciation · the year-end inventory count (COGS adjustment) · a full calendar year |
| **Taxes** | a monthly OS-114 whose figures tie to 2200 · a month with a GL/return difference (flag) · a use tax month · a 1099-NEC vendor over the threshold (and one paid by card, excluded) · Schedule C with a vehicle and a fixed asset · a loss year · the quarterly estimate helpers |
| **Review and feedback** | the owner accepts · the owner edits accounts · owner feedback that creates a **vendor rule**, then the next document from that vendor follows it · the owner rejects as "not business" · bulk accept of a 60-line card statement · Claude asks a question and the owner answers |
| **Bad input** | a blank page · a password-protected PDF · a non-financial document (a flyer) · a photo-quality image PDF · a document from the wrong year · a corrupt file |

A **coverage report** lists each scenario with the features it exercises, so a feature without a scenario is visible.

### 15.5 Business simulation (end-to-end sets)

Besides single scenarios, a **simulator** produces a whole, consistent period for the shop from a few parameters: seed, month(s), average daily sales, delivery share per platform, food-cost %, number of employees, and how messy the owner's paperwork is. It creates the daily Square reports, weekly supplier purchases, weekly platform statements, semi-monthly payroll, rent and utilities, owner draws, the monthly card and bank statements, and a few cash logs. **They are all consistent with each other:** every receipt appears on the right statement, and every payout appears on the bank statement. Presets:

| Preset | Contents | Use |
|---|---|---|
| `smoke-1-week` | ~25 documents, clean | a quick end-to-end check after a change |
| `jan-2026-scanned` | one month, ~140 documents, scanned | month close, OS-114 for January |
| `q1-2026-messy` | three months, messy + a few adversarial | reconciliation, feedback learning, the hardest extraction |
| `year-2026` | a full year, scanned | the year's statements, Schedule C/SE, CT-1040 figures, the 1099 list |

### 15.6 Evaluation (the scorecard)

`python -m testdata evaluate <sandbox>` (or **Run evaluation** on the screen) runs the real pipeline on a generated set and compares every stage with the answer key:

| Stage | Measure |
|---|---|
| Classify | document type and page-split accuracy |
| Extract | field accuracy per document type and difficulty (amounts exact to the cent); flagged vs silently wrong figures (**silently wrong must be 0**) |
| Propose | entries identical to the expected ones; wrong-account rate; owner edits needed |
| Match | matches found / missed / wrong; double-counted items (**must be 0**) |
| Reports | TB, IS, BS, CF identical to the cent |
| Tax | OS-114, Schedule C/SE and CT figures identical to the cent |
| Cost | Claude time and cost per document |

The result is an HTML **scorecard**, saved in `testdata\results\<date>-<set>.html` like the analyzer's Analysis-history reports, plus a JSON file for comparing runs. So a prompt change can be judged ("extraction of messy receipts 91% → 96%, cost +8%").

Two modes:
- **Mocked** (free, part of `pytest`): Claude's replies come from the answer key. This tests everything after extraction exactly: ledger, matching, reports and tax.
- **Live** (costs Claude usage): real extraction and proposals on the generated documents. Run when prompts or models change, and before a release.

### 15.7 Where it lives and how it is used

- **Code:** `WEB-APP\testdata\` (§12):
  - `scenarios\`: the YAML scenarios.
  - `truth.py`: events → expected ledger.
  - `layouts\`: HTML/CSS for each document source.
  - `render.py`: PDF and CSV output.
  - `degrade.py`: the scan look and handwriting.
  - `simulate.py`: the business simulation.
  - `evaluate.py`: the scorecard.
  - `fonts\`: the OFL handwriting fonts.
  - `__main__.py`: the command line.
- **Command line:**
  ```powershell
  python -m testdata list                                   # the scenario catalog and its coverage
  python -m testdata generate receipt-cash-split --difficulty messy --seed 7
  python -m testdata simulate jan-2026-scanned --sandbox jan26
  python -m testdata evaluate jan26 --live                  # real Claude calls; --mocked is free
  ```
- **Screen:** **Test data** `/test-data` (§11) lets you:
  - choose scenarios or a preset, a difficulty and a seed;
  - **Generate** into a sandbox, as a background job with progress;
  - open the sandbox and **Run evaluation**;
  - view past scorecards;
  - view any generated document next to its answer key.
- **New dependencies:** **Pillow** and **NumPy** (scan effects), **PyYAML** (scenarios, tax rules). No new paid service.

---

## 16. Security and privacy

- Local only (127.0.0.1), as the analyzer is. `data\` is kept out of git and backed up (a **Backup** button zips `books.db` + documents).
- Document images and text are sent to Anthropic through Claude Code, as the analyzer's pages are. Card numbers: only the last 4 digits are stored; the extraction prompt tells Claude never to output a full card or account number, and code masks any long digit run before saving.
- EIN/SSN are entered on the Setup screen, never extracted from scans, and not sent to Claude.

---

## 17. Risks

| Risk | Mitigation |
|---|---|
| Handwriting and faded thermal receipts misread | higher dpi, `[?]` marks, tie-outs, Tesseract cross-check, owner confirmation of every low-confidence figure |
| Double counting across receipt / statement / bank | posting-source rules (§4.2), clearing accounts, the Matcher, reconciliation before month close |
| Wrong account chosen | owner review, vendor rules learned from feedback, confidence thresholds for bulk accept |
| Tax rules change or are misapplied | yearly versioned rule files, drafts only, visible line-by-line sources, CPA review recommended |
| Marketplace facilitator and delivery-fee sales tax treatment | explicit open question (§18), separate revenue accounts so the treatment can be changed without rebooking |
| Claude Code CLI changes or slowness | one client module; jobs with timeouts and retries; per-document cost tracking; `CLAUDE_EFFORT=low` default as in the analyzer |
| Owner treats drafts as final returns | watermarks, a checklist of open questions per return, a status (draft / reviewed / filed) |

---

## 18. Decisions (confirmed 2026-09-29)

| # | Question | Decision | Effect on the plan |
|---|---|---|---|
| D1 | LLC tax classification | **Single-member LLC, disregarded entity** | Federal: **Schedule C + Schedule SE** (Form 1040), 1040-ES helper. CT: **CT-1040** figures + CT-1040ES helper. Form 1065 / CT-1065 and the S-corp path are **out of MVP 1** (§10.3–10.4 keep them as later options) |
| D2 | Accounting basis | **Accrual books, with a cash-basis report option** | Sales tax payable, tips payable, card clearing and platform receivable are accrued; Reports and the Schedule C worksheet get a *Cash basis* toggle (the CPA confirms which basis to file on) |
| D3 | POS system | **Square** | Phase 1 builds the `pos_statement` extractor for **Square** reports (Sales Summary / daily close-of-day, monthly statement) and a **Square CSV import** (transactions and payouts). Square fees are netted from payouts → booked to 6200 |
| D3b | Delivery platforms | **DoorDash, Uber Eats, Grubhub** | One extraction prompt per platform statement layout; sales booked gross to 4020 (one sub-account per platform), commissions and fees to 6210, payouts matched to the bank. Marketplace-facilitator sales tax treatment on OS-114 still to be confirmed with DRS/CPA (§10.2) |
| D4 | Bank statements | **Yes, PDF + CSV** | `bank_statement` is in Phase 1 (moved from Phase 3); the CSV is the posting source when available, the PDF is the supporting document and the reconciliation anchor |
| D5 | Payroll | **Payroll provider** | The app records wages, employer payroll taxes and liabilities from the provider's payroll reports (a `payroll_report` document type, Phase 3); the provider files 941 / CT-941 / W-2. Cash tips paid out go through 2210 |
| D6 | Sales tax filing frequency | **Monthly** | OS-114 worksheet per month; due-date reminders on the tax calendar; the frequency stays a Setup setting |
| D7 | Books start date | **Jan 1, 2026** (calendar year) | Opening balances wizard as of 2026-01-01; first full tax year from the app: **2026** (`tax_rules\2026\`) |
| D8 | Own delivery fee | **Yes, charged and taxed** | Account 4050, included in taxable gross receipts on OS-114 (confirm with CPA/DRS; the rate lives in `sales_tax.yaml`) |
| D9 | Capitalization threshold | **$2,500 per item** | Items above it → fixed-asset register + Form 4562 figures; below it → expensed. Setup setting |
| D10 | CPA review | **Yes** | Phase 6 adds a **CPA handoff package**: GL and TB export (CSV/Excel), the statements, the Schedule C / SE / CT worksheets with each line's source accounts, the fixed-asset register, the 1099 list and the open questions |

### Still to confirm with the CPA / CT DRS (not blocking development)

1. How DoorDash / Uber Eats / Grubhub (marketplace facilitator) sales appear on OS-114.
2. Sales tax on the shop's own delivery fee (D8 assumes taxable).
3. Filing basis for Schedule C (cash vs accrual) under D2.
4. Current CT dates and amounts: LLC annual report, Hartford personal property declaration, estimated tax due dates.

---

## 19. Implementation notes (2026-09-29)

Built in `..\Web-App` (see its `README.md`). What differs from, or adds to, the plan:

- **One Claude call per file** does the Classifier and Extractor steps together (`prompts\extract.txt`): it returns every document found in the file with its pages and fields. CSV exports skip Claude entirely.
- **2150 is used for every non-cash receipt** (card, debit card and check), not only card, so one rule settles all of them: the card *or bank* line debits 2150. A check that pays a booked invoice is *support only*.
- **Match hints are computed by code** (`books\match.py`) and a proposal that ignores one is refused (`books\propose.py`). Also detected: a receipt that arrives after its statement, the same statement month in CSV and PDF, and statement gaps.
- **Replay mode**: every Claude step can be replayed from a sandbox's answer key, so all 57 scenarios run in `pytest` for free (about 2.5 minutes). A live run of 5 scenarios with real Claude calls (a scanned receipt, a handwritten cash sheet, a card month with refunds, interest and fees, a check paying an invoice and a Square day) got every field and entry right, for $0.07–0.16 per scenario.
- **The evaluator compares the net effect per date and account**, so gross or netted lines both count as correct.
- **Not built yet**: the cash-basis report view (D2), filling the official fillable PDF forms, Form 1065 and the S-corp path, and PDFs over 12 pages (these must be split first). `tax_rules\2026\*.yaml` is marked `verified: false`.
