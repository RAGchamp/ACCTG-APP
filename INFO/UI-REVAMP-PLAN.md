# UI Revamp Plan — a professional look for every screen

**Date:** 2026-10-02
**App:** `C:\Daya\RAGchamp\SMALL-BIZ-ACCTG\Web-App`
**Spec:** `C:\Daya\RAGchamp\SMALL-BIZ-ACCTG\S-PAD\UI-design-specs.pdf` (one page, four screenshots, "DASHBOARD PAGE")
**Scope:** look and layout only. No change to routes, data, bookkeeping, Claude calls or the JSON APIs.

---

## 0. What the spec asks for

The spec shows the Dashboard from top to bottom and ends with **"Other pages need to follow same pattern"**.

| # | Spec item | What it shows |
|---|---|---|
| S1 | **Top nav bar** | Full-width dark navy bar. Left: the **RAG Champ** logo (green/red round mark + "RAG Champ" in yellow). Right: **SMALL BUSINESS ACCOUNTING AGENT** in yellow, italic, wide letter- and word-spacing. Same look as the top bar of `INFO\HOW-ACCT-AGENT-WORKS.html` (`.topnav`, `logo.svg`). |
| S2 | **Menu, aligned in center** | A light grey bar under it: *Dashboard Inbox Review Input docs Ledger Reconcile Reports Taxes Test data Setup* (active item darker) and the **Live books / Sandbox** dropdown on the right. |
| S3 | **Overview in a light yellow box** | The page heading block: business name, sub-line (address · entity · books from … · month), the action buttons (**Add documents**, **Review N**) and the four tiles (To review, Sales, Net income, Bank (GL)), all inside one light-yellow panel. |
| S4 | **Tables as today, light-blue header, striped rows** | The *Work to do* and *Next tax dates* cards keep their content and two-column layout. Their header row is **light blue**, and the rows below are **striped**. |
| S5 | **Other pages follow the same pattern** | Every screen: top bar → centered menu → yellow overview box → content cards/tables with light-blue headers and striped rows. |

---

## 1. Design decisions

### 1.1 Design tokens (one place: `static\app.css`)

All colours are CSS variables, so every page and both themes stay consistent:

| Token | Light | Dark (system dark mode) | Used for |
|---|---|---|---|
| `--sba-brand-navy` | `#04264d` | `#04264d` | top bar (same as the HOW doc) |
| `--sba-brand-yellow` | `#ffd60a` | `#ffd60a` | logo text, tagline |
| `--sba-menu-bg` | `#f1f3f5` | `#1f2328` | the menu bar |
| `--sba-overview-bg` | `#fff8dc` (light yellow) | `#3a3215` | overview box |
| `--sba-overview-border` | `#f1e2a4` | `#5c4f1f` | overview box border |
| `--sba-head-bg` | `#e3eefc` (light blue) | `#1d2e45` | table headers, card headers |
| `--sba-head-text` | `#0b2545` | `#dbe7f7` | header text |
| `--sba-stripe` | `rgba(13,110,253,.035)` | `rgba(255,255,255,.04)` | striped rows |
| `--sba-sandbox` | `#e8710a` | `#e8710a` | the sandbox bar and badges (unchanged) |

Bootstrap 5.3 stays (already loaded from jsDelivr). The new rules go in **`static\app.css`**, loaded after Bootstrap. The inline `<style>` in `base.html` moves there. The font stays the Bootstrap system stack (Segoe UI on Windows, as in the spec screenshots).

### 1.2 The page shell (`templates\base.html`)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ (logo) RAG Champ                         SMALL  BUSINESS  ACCOUNTING  AGENT      │  top bar, navy
├──────────────────────────────────────────────────────────────────────────────────┤
│ Sandbox: jan-2026-scanned - synthetic test data ... Back to the live books       │  orange, only in a sandbox
├──────────────────────────────────────────────────────────────────────────────────┤
│        Dashboard Inbox Review Input docs Ledger Reconcile Reports Taxes          │  menu, centered,
│        Test data Setup                                        [Live books ▾]     │  sticky
└──────────────────────────────────────────────────────────────────────────────────┘
┌─ overview (light yellow) ────────────────────────────────────────────────────────┐
│ Page title                                                  [primary] [secondary]│
│ sub-line                                                                         │
│ [tile] [tile] [tile] [tile]                                                      │
└──────────────────────────────────────────────────────────────────────────────────┘
┌─ card: light-blue header ──────────────┐ ┌─ card: light-blue header ─────────────┐
│ striped rows ...                       │ │ striped rows ...                      │
```

- **Top bar** (S1): `static\logo.svg` (copied from `INFO\logo.svg`, the same mark as the spec) links to the Dashboard. The tagline is on the right and hides below 576 px, where only the logo shows.
- **Menu** (S2): Bootstrap `navbar-expand-lg`. The links sit in a centered container (`justify-content-center`), and the books dropdown is pinned to the right edge, as in the screenshot. Below `lg` it collapses behind a toggler. The active item is dark and semi-bold with a 2 px navy underline. The old "Accounting Agent · live books" brand text goes away: the dropdown already says which books are open, and in a sandbox the orange bar says it too.
- **Sandbox bar**: kept, unchanged in meaning, moved **between the top bar and the menu**, so it can't be missed.
- **Sticky**: only the menu bar is sticky; the top bar scrolls away to save screen height on the Review screen. Sticky offsets (`.sticky-col`, `section` scroll margins) are set from a single CSS variable `--sba-sticky-top`.
- **Print**: the top bar, menu, sandbox bar, buttons and job box are hidden (as today). Overview colours print as a thin border only.

### 1.3 Reusable building blocks (`templates\_ui.html`, Jinja macros)

So every page follows the pattern with the same markup, not copy-paste:

| Macro / class | Renders |
|---|---|
| `overview(title, subtitle, actions)` + `{% call %}` body | The light-yellow box: title (h1, 1.35 rem), sub-line (muted, small), right-aligned action buttons, and the call body (tiles, filters). |
| `tile(label, value, note, href=None)` | A white tile inside the overview: small label, large value, small note (the spec's four tiles). |
| `.sba-card` | A card whose `.card-header` uses `--sba-head-bg` / `--sba-head-text`, with a header link on the right (e.g. *Taxes*). |
| `.sba-table` | `table table-sm table-striped table-hover align-middle`, `thead th` in `--sba-head-bg`; amounts right-aligned (`.num`, unchanged). |
| `.sba-list` | `list-group-flush` with striped items (for *Work to do*, which is a list, not a table). |

Tables that today have their header as the first `<tr><th>` get a real `<thead>`, so the header colour applies.

### 1.4 What is *not* striped

Striping is for **lists of records** (documents, entries, files, dates, accounts). It is **not** applied where a stripe would read as meaning:

- the **financial statements** on Reports (Income statement, Balance sheet …). They keep section rows (`table-light` → light blue) and bold totals, like a printed statement;
- the **proposal entry tables** on Review and the ledger-tagging tables on Input docs, which keep their debit/credit layout (header light blue, no stripes, balanced/not balanced row in green/red);
- key/value tables (extracted data fields).

---

## 2. Page by page

| Screen | Overview box (light yellow) | Below it |
|---|---|---|
| **Dashboard** `/` | Business name; *address · single-member LLC · books from … · month*; **Add documents**, **Review N**; the four tiles. (Exactly the spec.) | Two columns: *Work to do* (`.sba-list`, striped) and *Unmatched items* (`.sba-table`); *Next tax dates* (`.sba-table`, link *Taxes* in the header) and the usage/backup note. |
| **Inbox** | "Inbox"; *N files · M documents · K waiting in the Inbox folder*; **Process the inbox (K)**, *Run the Bookkeeper on documents still to propose*; tiles: files new/failed, to propose, to review, Claude calls & cost. | *Upload* and *Inbox folder* cards side by side; *Files* table (`.sba-table`). |
| **Review** | "Review"; *N in the queue · Q questions · A need attention*; **Bulk accept N safe**; queue filters (All / Questions / Attention) as a button group. | Three columns as today (queue · document · proposal). The queue is a striped list; the proposal card header is light blue. Keyboard shortcuts unchanged. |
| **Input docs** | "Review input documents"; one-line help; the **Live / Sandbox** check boxes and sandbox dropdown inside the box; **Review** button. | Filters + the document table (`.sba-table`). On the boxes page: one card per document with a light-blue header; the four sections' tables use light-blue headers (striped only for the statement and tax tables). |
| **Ledger** | "General Ledger / Journal"; period; date/account/search filters inside the box; tiles: entries in period, debits = credits ✓. | *New manual journal entry* (card); account ledger tables; *Entries* (`.sba-table`; reversed rows stay struck through). |
| **Reconcile** | "Reconcile 2026-01" + status badge; month buttons; tiles: bank difference, card difference, open items; **Close / Reopen**. | *Statement vs GL* table and *Open items* table (`.sba-table`). |
| **Reports** | Business name + report title + period · accrual basis; report selector (button group), dates, **Show**, **CSV**, **Print**. | The statement (formal, not striped, §1.4) in a white card with a light-blue header; the self-check line (✓/✗) under it. |
| **Taxes** | "Taxes 2026" + DRAFT badge; the rules-not-verified warning as one line; **Download the CPA package**, **Print**. | Tabs (OS-114, Schedule C/SE, 4562, 1099-NEC, Connecticut, Calendar); each tab's tables `.sba-table` (Schedule C lines keep bold totals). |
| **Test data** | "Synthetic test data"; one-line help; **Feature coverage**; tiles: scenarios, sandboxes, last scorecard PASS/FAIL. | *Generate* card; *Sandboxes* and *Scorecards* tables (`.sba-table`). Answer key page: same shell; file list striped. |
| **Setup** | "Setup"; business name · books start · tax year; tiles: opening balances posted ✓/✗, active accounts, vendor rules. | Cards with light-blue headers: Business profile, Opening balances, Vendor rules (table), Chart of Accounts (table), Audit log (striped list). |

Everything the pages show today stays, with the same IDs and `onclick` handlers, so `static\app.js`, `static\doc_review.js` and the tests keep working.

---

## 3. Implementation

### 3.1 Files

| File | Change |
|---|---|
| `static\logo.svg` (new) | Copy of `INFO\logo.svg` (the RAG Champ mark in the spec). |
| `static\app.css` (new) | Tokens (§1.1), top bar, menu, sandbox bar, overview, tiles, `.sba-card`, `.sba-table`, `.sba-list`, dark mode, print, small-screen rules. The inline `<style>` of `base.html` moves here. |
| `templates\_ui.html` (new) | Macros `overview`, `tile` (§1.3). |
| `templates\base.html` | New shell: top bar → sandbox bar → centered menu with the books dropdown on the right; `app.css`; `--sba-sticky-top`. |
| `templates\dashboard.html` | Spec layout (S3, S4). |
| `templates\inbox.html`, `review.html`, `doc_review.html`, `doc_review_show.html`, `ledger.html`, `reconcile.html`, `reports.html`, `taxes.html`, `test_data.html`, `sandbox_view.html`, `setup.html` | Overview box + `.sba-card` / `.sba-table`; real `<thead>`s; no logic changes. |
| `static\app.js`, `templates\review.html` (inline script), `static\doc_review.js` | Tables built in JavaScript (proposal entries, document list) get the same classes. |
| `tests\test_web.py` | A check that every page has the top bar, the menu, exactly one overview box, and that table headers use `thead`. |
| `INFO\HOW-ACCT-AGENT-WORKS.html` | Update the menu mock-up in §2 and the screen descriptions where the layout moved (e.g. filters now inside the overview box). |

### 3.2 Dark mode

`base.html` already follows the system theme (`data-bs-theme`). Every new colour has a dark value (§1.1), so the yellow box and the blue headers never show as bright patches on a dark page. The top bar is the same navy in both themes.

### 3.3 Accessibility and polish

- Text contrast at least 4.5:1 in both themes, including yellow on navy and navy on light blue.
- The logo has `alt="RAG Champ"`; the menu is a `<nav>` with `aria-label`; the active item has `aria-current="page"`.
- Focus rings stay visible; tables keep `.table-responsive` so a phone scrolls inside the table, not the page.
- Numbers use tabular figures and right alignment (`.num`, unchanged).

---

## 4. Phases

1. **Shell:** `app.css`, `logo.svg`, `base.html` (top bar, sandbox bar, centered menu), `_ui.html`. Every page immediately gets S1/S2.
2. **Dashboard** to the spec (S3, S4), as the reference page.
3. **Work screens:** Inbox, Review, Input docs (list + boxes), Ledger, Reconcile.
4. **Output screens:** Reports, Taxes, Setup, Test data, Answer key.
5. **JavaScript-built tables**, dark mode pass, print pass, small-screen pass.
6. **Tests and docs:** `test_web.py` checks; HOW doc update; screenshots of each page (light and dark) checked by the owner.

Each phase leaves the app working; the full test suite runs after each one.

---

## 5. Decisions (confirmed by the owner 2026-10-02)

Each question was answered one by one; the owner chose the default every time. The last column records what was considered and not chosen.

| # | Question | Decision | Alternatives (not chosen) |
|---|---|---|---|
| U1 | "Aligned in center" for the menu | Menu links centered; books dropdown on the right edge (as in the screenshot) | Links and dropdown together as one centered group; or links left-aligned in a centered fixed-width container |
| U2 | Where the orange sandbox bar goes | Between the top bar and the menu | Above the top bar (as today); or a sandbox badge inside the menu bar only |
| U3 | Dark mode | Keep following the system theme, with dark versions of yellow and blue | Always light (as in the spec screenshots) |
| U4 | What stays on screen when scrolling | Only the menu bar is sticky | Top bar and menu both sticky; nothing sticky |
| U5 | Striping of financial statements and entry tables | Not striped (formal statement look); light-blue headers only | Striped like every other table |
| U6 | Overview box on pages other than the Dashboard | Title, sub-line, actions, plus tiles where the page has useful figures (table in §2) | Title, sub-line and actions only (no new tiles) |
| U7 | Page width | Full width (as today), overview and cards with 16–24 px side padding | A centered max-width container (e.g. 1400 px) on wide screens |
