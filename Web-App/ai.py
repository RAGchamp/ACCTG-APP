"""Every Claude call of the app goes through here (plan §3 agent steps).

Two modes, chosen per business:
- live:   `claude -p` through claude_client.py (the analyzer's client), using
          the local Claude Code login. Page images are read with the Read tool.
- replay: the reply comes from the sandbox's answer_key.json, written by the
          synthetic test data generator (plan §15.6 "mocked"). The prompt is
          still built and saved, so prompt changes are visible in tests.

Every prompt and reply is saved next to its document (plan P3; the analyzer's
Prompt-History idea) and recorded in the ai_calls table with time and cost.
"""

import json
import logging
import re
import time
from pathlib import Path

import config
from books import db
from claude_client import ClaudeError, run_claude, run_claude_with_image

log = logging.getLogger(__name__)
JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)\s*```", re.S)


def parse_json_reply(text):
    """The JSON object in Claude's ```json block (or the whole reply)."""
    match = JSON_BLOCK_RE.search(text or "")
    raw = match.group(1) if match else (text or "").strip()
    try:
        return json.loads(raw)
    except ValueError:
        start, end = raw.find("{"), raw.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except ValueError:
                pass
    raise ClaudeError("Claude's reply had no readable JSON block.")


def as_reply(obj):
    """The form a real reply takes, for replayed answers."""
    return "```json\n" + json.dumps(obj, indent=1) + "\n```"


class AnswerKey:
    def __init__(self, path):
        self.path = Path(path)
        self.data = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"files": {}}

    def file(self, sha):
        return self.data.get("files", {}).get(sha)

    def reply(self, kind, sha, part=None, version=None):
        f = self.file(sha)
        if not f:
            raise ClaudeError(f"Replay: file {sha[:12]} is not in the answer key.")
        if kind == "extract":
            return f["extract_reply"]
        doc = (f.get("documents") or {}).get(str(part))
        if not doc:
            raise ClaudeError(f"Replay: document {part} of file {sha[:12]} is not in the answer key.")
        if kind == "propose":
            return doc["propose_reply"]
        if kind == "revise":
            if not doc.get("revise_reply"):
                raise ClaudeError("Replay: the answer key has no revised proposal for this document.")
            return doc["revise_reply"]
        raise ClaudeError(f"Replay: no recorded {kind} replies.")


class AI:
    def __init__(self, biz, mode=None):
        self.biz = biz
        self.mode = mode or biz.ai_mode
        self._key = None

    @property
    def key(self):
        if self._key is None:
            self._key = AnswerKey(self.biz.answer_key_path)
        return self._key

    def call(self, kind, prompt, system, *, folder, file_id=None, document_id=None,
             images=None, replay=None, timeout=None):
        """Run one step; returns (parsed JSON, meta). `folder` is where the prompt and
        reply are saved (the file's folder); `images` are PNG names in `folder` Claude
        must read; `replay` = (sha, part) for the answer key."""
        folder = Path(folder)
        calls = folder / "calls"
        calls.mkdir(parents=True, exist_ok=True)
        n = len(list(calls.glob("*.prompt.txt"))) + 1
        stem = f"{n:03d}-{kind}" + (f"-doc{document_id}" if document_id else "")
        prompt_path, reply_path = calls / f"{stem}.prompt.txt", calls / f"{stem}.reply.txt"
        prompt_path.write_text(f"=== SYSTEM ===\n{system}\n\n=== PROMPT ===\n{prompt}", encoding="utf-8")
        config.INPUT_FILE.write_text(prompt, encoding="utf-8")
        started, cost, ok, error, reply = time.monotonic(), 0.0, True, None, ""
        try:
            if self.mode == "replay":
                sha, part = replay if replay else (None, None)
                reply = as_reply(self.key.reply(kind, sha, part))
            elif images:
                # The Read tool reads the images from `folder`; claude_client's image call
                # takes no system prompt, so it leads the prompt.
                reply, meta = run_claude_with_image(system + "\n\n" + prompt, folder,
                                                    timeout or config.EXTRACT_TIMEOUT, effort=config.CLAUDE_EFFORT)
                cost = meta.get("cost_usd") or 0.0
            else:
                reply = run_claude(prompt, system, timeout or config.PROPOSE_TIMEOUT, effort=config.CLAUDE_EFFORT)
            reply_path.write_text(reply, encoding="utf-8")
            config.OUTPUT_FILE.write_text(reply, encoding="utf-8")
            data = parse_json_reply(reply)
        except ClaudeError as exc:
            ok, error = False, str(exc)
            reply_path.write_text(f"ERROR: {exc}\n\n{reply}", encoding="utf-8")
            raise
        finally:
            seconds = round(time.monotonic() - started, 1)
            with self.biz.session() as conn:
                conn.execute(
                    "INSERT INTO ai_calls(kind, file_id, document_id, mode, prompt_path, reply_path, seconds,"
                    " cost_usd, ok, error, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (kind, file_id, document_id, self.mode, str(prompt_path), str(reply_path), seconds, cost,
                     int(ok), error, db.now()))
            log.info("%s %s (%s) in %.1fs ok=%s", self.mode, kind, stem, seconds, ok)
        return data, {"seconds": seconds, "cost_usd": cost, "prompt_path": str(prompt_path)}
