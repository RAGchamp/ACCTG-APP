-- The books of one business (plan §8). Money is integer cents.
-- Posted journal entries are never updated or deleted, only reversed (P6).

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL                 -- JSON
);

CREATE TABLE IF NOT EXISTS accounts (
    number          TEXT PRIMARY KEY,   -- "5010"
    name            TEXT NOT NULL,
    type            TEXT NOT NULL CHECK (type IN ('asset','liability','equity','revenue','cogs','expense')),
    subtype         TEXT,               -- cash, clearing, fixed_asset, contra_asset, contra_revenue, ...
    tax_line        TEXT,               -- Schedule C line, e.g. "20b"
    cash_flow_class TEXT,               -- cash / operating / investing / financing
    description     TEXT,               -- "use for" hint sent to Claude
    active          INTEGER NOT NULL DEFAULT 1
);

-- An uploaded file (PDF or CSV). One file may hold several documents.
CREATE TABLE IF NOT EXISTS files (
    id          INTEGER PRIMARY KEY,
    sha256      TEXT NOT NULL UNIQUE,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    kind        TEXT NOT NULL,          -- pdf / csv
    source      TEXT NOT NULL,          -- upload / inbox / testdata
    synthetic   INTEGER NOT NULL DEFAULT 0,
    page_count  INTEGER,
    scanned     INTEGER,                -- 1 = image-only PDF
    status      TEXT NOT NULL,          -- new / extracting / extracted / failed
    error       TEXT,
    added_at    TEXT NOT NULL,
    seq         INTEGER                 -- processing order (sandbox: the scenario's order)
);

CREATE TABLE IF NOT EXISTS pages (
    file_id    INTEGER NOT NULL REFERENCES files(id),
    page_no    INTEGER NOT NULL,
    image_path TEXT,
    text       TEXT,
    PRIMARY KEY (file_id, page_no)
);

-- A logical document found in a file (a receipt, a statement ...).
CREATE TABLE IF NOT EXISTS documents (
    id            INTEGER PRIMARY KEY,
    file_id       INTEGER NOT NULL REFERENCES files(id),
    part          INTEGER NOT NULL DEFAULT 1,   -- nth document in the file
    pages         TEXT NOT NULL,                -- JSON [1, 2]
    doc_type      TEXT NOT NULL,
    doc_date      TEXT,
    party         TEXT,                         -- vendor / payee / platform / bank
    total_cents   INTEGER,
    extraction    TEXT NOT NULL,                -- JSON, the checked extraction
    flags         TEXT NOT NULL DEFAULT '[]',   -- JSON [{"level","field","message"}]
    status        TEXT NOT NULL,                -- extracted / proposed / needs_review / posted / support / rejected
    reject_reason TEXT,
    created_at    TEXT NOT NULL,
    UNIQUE (file_id, part)
);

CREATE TABLE IF NOT EXISTS ai_calls (
    id          INTEGER PRIMARY KEY,
    kind        TEXT NOT NULL,          -- extract / propose / revise / explain / vary
    file_id     INTEGER,
    document_id INTEGER,
    mode        TEXT NOT NULL,          -- live / replay
    prompt_path TEXT,
    reply_path  TEXT,
    seconds     REAL,
    cost_usd    REAL,
    ok          INTEGER NOT NULL,
    error       TEXT,
    at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL REFERENCES documents(id),
    version     INTEGER NOT NULL,
    source      TEXT NOT NULL,          -- claude / replay / manual / revised
    entries     TEXT NOT NULL,          -- JSON list of entries
    support_only INTEGER NOT NULL DEFAULT 0,
    confidence  TEXT,
    reason      TEXT,
    questions   TEXT NOT NULL DEFAULT '[]',
    errors      TEXT NOT NULL DEFAULT '[]',   -- code checks that failed
    matches     TEXT NOT NULL DEFAULT '[]',   -- the match hints it relies on (books/match.py)
    rule_suggestion TEXT,                     -- JSON {vendor, rule, account} from the Reviser
    ledger_mark INTEGER NOT NULL DEFAULT 0,   -- last journal entry id when it was made (stale check)
    status      TEXT NOT NULL,          -- open / superseded / accepted / rejected
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_entries (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    period      TEXT NOT NULL,          -- YYYY-MM
    memo        TEXT,
    kind        TEXT NOT NULL,          -- document / manual / opening / reversal / adjustment
    document_id INTEGER REFERENCES documents(id),
    proposal_id INTEGER REFERENCES proposals(id),
    reverses    INTEGER REFERENCES journal_entries(id),
    posted_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS journal_lines (
    id        INTEGER PRIMARY KEY,
    entry_id  INTEGER NOT NULL REFERENCES journal_entries(id),
    line_no   INTEGER NOT NULL,
    account   TEXT NOT NULL REFERENCES accounts(number),
    debit     INTEGER NOT NULL DEFAULT 0 CHECK (debit >= 0),
    credit    INTEGER NOT NULL DEFAULT 0 CHECK (credit >= 0),
    memo      TEXT,
    party     TEXT,
    ref       TEXT,                     -- the document line it came from ("txn 7")
    CHECK (NOT (debit > 0 AND credit > 0))
);
CREATE INDEX IF NOT EXISTS ix_lines_account ON journal_lines(account);
CREATE INDEX IF NOT EXISTS ix_lines_entry ON journal_lines(entry_id);

-- Posted entries are append-only.
CREATE TRIGGER IF NOT EXISTS no_update_lines BEFORE UPDATE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'posted journal lines cannot be changed; reverse the entry'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_lines BEFORE DELETE ON journal_lines
BEGIN SELECT RAISE(ABORT, 'posted journal lines cannot be deleted; reverse the entry'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_entries BEFORE DELETE ON journal_entries
BEGIN SELECT RAISE(ABORT, 'posted journal entries cannot be deleted; reverse the entry'); END;

-- Supporting documents and the statement lines they explain (plan §4.2, §7).
CREATE TABLE IF NOT EXISTS matches (
    id              INTEGER PRIMARY KEY,
    kind            TEXT NOT NULL,      -- receipt_card / receipt_bank / check_bank / payout / card_payment / support
    document_id     INTEGER NOT NULL REFERENCES documents(id),   -- the document being processed
    doc_ref         TEXT,               -- its line ("txn 7")
    other_document_id INTEGER REFERENCES documents(id),
    other_ref       TEXT,
    amount          INTEGER,
    method          TEXT,               -- amount+date / check number / claude / owner
    confidence      TEXT,
    confirmed       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS vendors (
    name            TEXT PRIMARY KEY,
    aliases         TEXT NOT NULL DEFAULT '[]',
    default_account TEXT,
    is_1099         INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS vendor_rules (
    id          INTEGER PRIMARY KEY,
    vendor      TEXT NOT NULL,
    rule_text   TEXT NOT NULL,
    account     TEXT,
    feedback_id INTEGER,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id              INTEGER PRIMARY KEY,
    document_id     INTEGER NOT NULL REFERENCES documents(id),
    proposal_before INTEGER,
    proposal_after  INTEGER,
    text            TEXT NOT NULL,
    rule_text       TEXT,
    rule_status     TEXT,               -- proposed / accepted / once
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS periods (
    month     TEXT PRIMARY KEY,         -- YYYY-MM
    status    TEXT NOT NULL,            -- open / closed
    closed_at TEXT,
    note      TEXT
);

CREATE TABLE IF NOT EXISTS reconciliations (
    id                INTEGER PRIMARY KEY,
    account           TEXT NOT NULL,
    month             TEXT NOT NULL,
    statement_closing INTEGER,
    gl_balance        INTEGER NOT NULL,
    difference        INTEGER,
    document_id       INTEGER,
    at                TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fixed_assets (
    id            INTEGER PRIMARY KEY,
    description   TEXT NOT NULL,
    account       TEXT NOT NULL,
    cost          INTEGER NOT NULL,
    placed_in_service TEXT NOT NULL,
    life_years    INTEGER NOT NULL DEFAULT 7,
    method        TEXT NOT NULL DEFAULT 'SL',
    document_id   INTEGER,
    journal_line_id INTEGER
);

CREATE TABLE IF NOT EXISTS tax_returns (
    id            INTEGER PRIMARY KEY,
    form          TEXT NOT NULL,
    period        TEXT NOT NULL,
    figures       TEXT NOT NULL,
    rules_version TEXT NOT NULL,
    generated_at  TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS audit_log (
    id     INTEGER PRIMARY KEY,
    at     TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT
);

-- Ledger trail (INFO: ledger-trail page): one row per change to the ledger - a posted entry,
-- a reversal, a month closed or reopened - with where it came from and a snapshot of its
-- lines. Append-only, like the journal: the database refuses edits and deletes.
CREATE TABLE IF NOT EXISTS ledger_trail (
    id           INTEGER PRIMARY KEY,
    at           TEXT NOT NULL,          -- when the change was made
    action       TEXT NOT NULL,          -- post / reverse / close_month / reopen_month
    entry_id     INTEGER,                -- the journal entry posted (post, reverse)
    reverses     INTEGER,                -- the entry a reversal undoes
    kind         TEXT,                   -- document / manual / opening / reversal / adjustment
    entry_date   TEXT,                   -- the entry's accounting date
    period       TEXT,                   -- YYYY-MM (the entry's, or the month closed / reopened)
    memo         TEXT,
    document_id  INTEGER,
    total_cents  INTEGER,                -- sum of the debits
    lines        TEXT,                   -- JSON snapshot [{account, debit, credit, memo, party, ref}]
    origin       TEXT NOT NULL,          -- where in the app it came from (screen and action)
    note         TEXT                    -- the reason given (reopen, override, re-processing ...)
);
CREATE INDEX IF NOT EXISTS ix_trail_at ON ledger_trail(at);
CREATE INDEX IF NOT EXISTS ix_trail_entry ON ledger_trail(entry_id);
CREATE TRIGGER IF NOT EXISTS no_update_trail BEFORE UPDATE ON ledger_trail
BEGIN SELECT RAISE(ABORT, 'the ledger trail cannot be changed'); END;
CREATE TRIGGER IF NOT EXISTS no_delete_trail BEFORE DELETE ON ledger_trail
BEGIN SELECT RAISE(ABORT, 'the ledger trail cannot be deleted'); END;
