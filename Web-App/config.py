"""Central settings for the Small Business Accounting Agent (MVP 1).

Plan: ..\\INFO\\SMALL-BIZ-ACCTG-MVP1-PLAN.md. The Claude settings follow the
Annual Report Analyzer's config.py (same CLI, same environment variables idea).
Path-like settings can be overridden with environment variables.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent

# The owner drops scanned PDFs / CSV exports here and clicks ↻ (plan §5.1).
INBOX_DIR = Path(os.environ.get("SBA_INBOX_DIR", PROJECT_DIR / "Inbox"))
# books.db + documents of the real business and of every sandbox (plan §8, §15.3).
DATA_DIR = Path(os.environ.get("SBA_DATA_DIR", BASE_DIR / "data"))
LIVE_DIR = DATA_DIR / "live"
SANDBOX_DIR = DATA_DIR / "sandbox"
ACTIVE_FILE = DATA_DIR / "active.json"

PROMPTS_DIR = BASE_DIR / "prompts"
SCHEMAS_DIR = BASE_DIR / "schemas"
TAX_RULES_DIR = BASE_DIR / "tax_rules"
TESTDATA_DIR = BASE_DIR / "testdata"
SCENARIOS_DIR = TESTDATA_DIR / "scenarios"
RESULTS_DIR = TESTDATA_DIR / "results"
# Owner feedback on input documents, one JSON file each (INFO\DOC-REVIEW-REPROCESS-PLAN.md §4.4).
FEEDBACK_DIR = Path(os.environ.get("SBA_FEEDBACK_DIR", BASE_DIR / "feedback"))
LOG_DIR = BASE_DIR / "logs"
# Last prompt and reply, as in the analyzer (every prompt is also kept per document).
INPUT_FILE = BASE_DIR / "claude-prompt-input.txt"
OUTPUT_FILE = BASE_DIR / "Claude-prompt-output.txt"

for _d in (INBOX_DIR, DATA_DIR, LIVE_DIR, SANDBOX_DIR, LOG_DIR, RESULTS_DIR, FEEDBACK_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# --- Claude Code CLI (copied pattern from ANN-RPT-ANALYZER\WEB-APP\config.py) ---
CLAUDE_EXE = os.environ.get("CLAUDE_EXE", r"C:\Users\dayam\.local\bin\claude.exe")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL") or None
CLAUDE_EXTRA_FLAGS = ["--tools", "", "--no-session-persistence"]
CLAUDE_VIA_POWERSHELL = os.environ.get("CLAUDE_VIA_POWERSHELL") == "1"
CLAUDE_EFFORT = os.environ.get("SBA_CLAUDE_EFFORT", "low")
if CLAUDE_EFFORT.lower() == "default":
    CLAUDE_EFFORT = None
EXTRACT_TIMEOUT = 300
PROPOSE_TIMEOUT = 300
# "live" = real Claude calls; "replay" = answers from a sandbox's answer key
# (synthetic test data, plan §15.6). A sandbox chooses its own mode.
DEFAULT_AI_MODE = os.environ.get("SBA_AI_MODE", "live")

# --- Page images (plan §5.2, from the analyzer's OCR settings) ---
PAGE_DPI = 200                 # scanned pages (receipts, handwriting); the analyzer used 150 for typed pages
RETRY_DPI = 300                # "Re-extract at higher resolution"
EXTRACT_WORKERS = int(os.environ.get("SBA_EXTRACT_WORKERS", "4"))
MAX_PAGES_PER_CALL = 6         # a longer statement is extracted in chunks

# --- Bookkeeping defaults (plan §17/§18 decisions) ---
CAPITALIZATION_THRESHOLD_CENTS = 250_000     # D9: $2,500 per item
UNMATCHED_WARNING_DAYS = 45
MATCH_DAYS_BEFORE = 3
MATCH_DAYS_AFTER = 10
FEEDBACK_MAX_CHARS = 400
# Review input documents screen (DOC-REVIEW-REPROCESS-PLAN.md, decisions R9 and §4.1).
DOC_REVIEW_MAX = 30
FEEDBACK_TEXT_MAX = 1000
VIEW_IMAGE_WIDTH = 1000        # px of the resized page images on that screen
SIMILAR_ENTRIES = 5

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "5050"))
