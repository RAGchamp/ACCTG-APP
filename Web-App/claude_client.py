r"""Run one fresh, stateless Claude Code CLI call (`claude -p`).

COPIED unchanged from ANN-RPT-ANALYZER\WEB-APP\claude_client.py on 2026-09-29
(plan §2). Keep the two in step until they move into a shared library.

Same pattern as basic-chatbot/app.py: a new `claude -p` process per
request with a custom --system-prompt, using the local Claude Code login,
so no API key is needed.

One deliberate change: the prompt goes to the CLI on **stdin** instead of
inside a PowerShell -Command string. Extracted notes can be tens of
thousands of characters, which would exceed the Windows command-line limit
(~32K chars), and stdin also needs no quote escaping. Set
CLAUDE_VIA_POWERSHELL=1 to use basic-chatbot's original invocation.

With `on_progress`, the reply is streamed (`--output-format stream-json`) and
the callback receives the text written so far, so the browser can show the
answer while Claude is still writing it.
"""

import json
import logging
import re
import subprocess
import threading
import time

import config

log = logging.getLogger(__name__)

POWERSHELL_MAX_PROMPT_CHARS = 25_000
# Used by the PowerShell path: PowerShell 5.1 drops empty-string arguments,
# so `--tools ""` can't be passed through it. Block tools by name instead.
POWERSHELL_DISALLOWED_TOOLS = "Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,NotebookEdit,Agent"


PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


class ClaudeError(Exception):
    pass


def fill_prompt(name, **values):
    """Load prompts/<name> and replace its {{KEY}} placeholders in one pass,
    so values that contain placeholder-like text are never substituted again."""
    template = (config.PROMPTS_DIR / name).read_text(encoding="utf-8")
    return PLACEHOLDER_RE.sub(lambda m: str(values.get(m.group(1), m.group(0))), template)


def system_prompt(name):
    return (config.PROMPTS_DIR / name).read_text(encoding="utf-8").strip()


def _base_args(system_prompt, effort=None):
    args = [config.CLAUDE_EXE, "-p", "--system-prompt", system_prompt]
    if config.CLAUDE_MODEL:
        args += ["--model", config.CLAUDE_MODEL]
    if effort:
        args += ["--effort", effort]
    return args


def _run_stdin(prompt, system_prompt, timeout, effort=None):
    args = _base_args(system_prompt, effort) + config.CLAUDE_EXTRA_FLAGS
    return subprocess.run(
        args,
        input=prompt,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=config.BASE_DIR,
    )


def _run_streaming(prompt, system_prompt, timeout, effort, on_progress):
    """Like _run_stdin, but reads Claude Code's stream-json events as they come.

    Calls on_progress(phase, text): phase is "thinking" until the first answer
    text arrives, then "writing"; text is the whole answer so far. Returns a
    CompletedProcess whose stdout is the final answer, so run_claude's checks
    work unchanged.
    """
    args = (_base_args(system_prompt, effort) + config.CLAUDE_EXTRA_FLAGS
            + ["--output-format", "stream-json", "--verbose", "--include-partial-messages"])
    proc = subprocess.Popen(
        args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace", cwd=config.BASE_DIR,
    )
    timed_out = threading.Event()

    def on_timeout():
        timed_out.set()
        proc.kill()

    timer = threading.Timer(timeout, on_timeout)
    timer.start()
    stderr_chunks = []
    threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read()), daemon=True).start()

    def feed_prompt():
        try:
            proc.stdin.write(prompt)
            proc.stdin.close()
        except OSError:
            pass   # the process died; its exit code and stderr tell the story

    threading.Thread(target=feed_prompt, daemon=True).start()

    text, final, is_error = [], None, False
    try:
        for line in proc.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("type")
            if kind == "stream_event":
                inner = event.get("event") or {}
                block = inner.get("content_block") or {}
                if inner.get("type") == "content_block_start" and block.get("type") == "thinking":
                    on_progress("thinking", "".join(text))
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta":
                    text.append(delta.get("text", ""))
                    on_progress("writing", "".join(text))
            elif kind == "result":
                final = event.get("result")
                is_error = bool(event.get("is_error"))
        returncode = proc.wait()
    finally:
        timer.cancel()
    if timed_out.is_set():
        raise subprocess.TimeoutExpired(args, timeout)
    reply = final if final is not None else "".join(text)
    stderr = "".join(stderr_chunks)
    if is_error and returncode == 0:
        returncode, stderr = 1, reply or stderr
    return subprocess.CompletedProcess(args, returncode, reply, stderr)


def _run_powershell(prompt, system_prompt, timeout, effort=None):
    """basic-chatbot's original invocation. Single-quoted PowerShell strings
    never interpolate; the only escape needed is doubling single quotes."""
    if len(prompt) > POWERSHELL_MAX_PROMPT_CHARS:
        raise ClaudeError(
            f"Prompt is {len(prompt):,} chars - too long for the PowerShell "
            "command line. Unset CLAUDE_VIA_POWERSHELL to send it via stdin."
        )
    escaped_prompt = prompt.replace("'", "''")
    escaped_system_prompt = system_prompt.replace("'", "''")
    model = f" --model '{config.CLAUDE_MODEL}'" if config.CLAUDE_MODEL else ""
    if effort:
        model += f" --effort '{effort}'"
    ps_command = (
        f"& '{config.CLAUDE_EXE}' -p '{escaped_prompt}' "
        f"--system-prompt '{escaped_system_prompt}'{model} "
        f"--disallowedTools '{POWERSHELL_DISALLOWED_TOOLS}' --no-session-persistence"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", ps_command],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
        cwd=config.BASE_DIR,
    )


def run_claude(prompt, system_prompt, timeout, effort=None, on_progress=None):
    """Return Claude's reply text, or raise ClaudeError with a readable
    message (same error cases basic-chatbot handles).

    effort: Claude Code --effort level (low/medium/high/...), None = its default.
    on_progress: stream the reply; see _run_streaming. Ignored in PowerShell mode.
    """
    started = time.monotonic()
    try:
        if config.CLAUDE_VIA_POWERSHELL:
            result = _run_powershell(prompt, system_prompt, timeout, effort)
        elif on_progress:
            result = _run_streaming(prompt, system_prompt, timeout, effort, on_progress)
        else:
            result = _run_stdin(prompt, system_prompt, timeout, effort)
    except FileNotFoundError:
        raise ClaudeError(
            f"Claude Code CLI not found at {config.CLAUDE_EXE}. "
            "Set the CLAUDE_EXE environment variable to its path."
        )
    except subprocess.TimeoutExpired:
        raise ClaudeError(f"Claude Code took longer than {timeout}s to respond.")

    elapsed = time.monotonic() - started
    log.info("claude -p finished in %.1fs (prompt %d chars, effort %s, exit %s)",
             elapsed, len(prompt), effort or "default", result.returncode)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        message = "Error running Claude Code CLI."
        if detail:
            message += f"\n\n{detail[:2000]}"
        raise ClaudeError(message)

    reply = (result.stdout or "").strip()
    if not reply:
        raise ClaudeError("Claude Code returned an empty response.")
    return reply


def image_args(model=None, effort=None):
    """`claude -p` that may read image files: only the Read tool is enabled."""
    args = [config.CLAUDE_EXE, "-p", "--tools", "Read", "--no-session-persistence",
            "--output-format", "json"]
    model = model or config.CLAUDE_MODEL
    if model:
        args += ["--model", model]
    if effort:
        args += ["--effort", effort]
    return args


def run_claude_with_image(prompt, image_dir, timeout, model=None, effort=None):
    """Ask Claude about image file(s) in `image_dir` (the prompt names them).

    Runs with the Read tool only and with `image_dir` as the working folder,
    so the files it is asked to read are right there. Returns (reply text,
    {"seconds", "cost_usd", "model"}); raises ClaudeError."""
    args = image_args(model, effort)
    started = time.monotonic()
    try:
        result = subprocess.run(args, input=prompt, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=timeout,
                                cwd=str(image_dir))
    except FileNotFoundError:
        raise ClaudeError(f"Claude Code CLI not found at {config.CLAUDE_EXE}.")
    except subprocess.TimeoutExpired:
        raise ClaudeError(f"Claude Code took longer than {timeout}s to read the page.")
    try:
        data = json.loads(result.stdout or "{}")
    except ValueError:
        data = {}
    reply = (data.get("result") or "").strip()
    if result.returncode != 0 or data.get("is_error") or not reply:
        detail = (reply or result.stderr or result.stdout or "").strip()[:1000]
        raise ClaudeError(f"Claude could not read the page. {detail}")
    meta = {"seconds": round(time.monotonic() - started, 1),
            "cost_usd": round(float(data.get("total_cost_usd") or 0), 4),
            "model": ", ".join((data.get("modelUsage") or {}).keys())}
    return reply, meta
