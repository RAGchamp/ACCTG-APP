"""Which business's books are open: the real one ("live") or a sandbox (plan §15.3).

Each business is a folder: books.db, documents\\ (the files, page images,
prompts and replies) and, for a sandbox made from synthetic test data,
answer_key.json. Generated data never goes into the live books.
"""

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

import config
from books import db


@dataclass(frozen=True)
class Business:
    name: str              # "live" or the sandbox name
    kind: str              # live / sandbox
    root: Path
    ai_mode: str           # live / replay

    @property
    def db_path(self):
        return self.root / "books.db"

    @property
    def documents_dir(self):
        return self.root / "documents"

    @property
    def answer_key_path(self):
        return self.root / "answer_key.json"

    @property
    def is_sandbox(self):
        return self.kind == "sandbox"

    def session(self):
        return db.session(self.db_path)


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,40}$")


def _meta_path(root):
    return Path(root) / "business.json"


def _load(root, kind, name):
    meta = {}
    try:
        meta = json.loads(_meta_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    mode = meta.get("ai_mode") or (config.DEFAULT_AI_MODE if kind == "live" else "replay")
    biz = Business(name=name, kind=kind, root=Path(root), ai_mode=mode)
    if not biz.db_path.exists():
        db.init_db(biz.db_path)
    db.ensure_schema(biz.db_path)
    return biz


def live():
    return _load(config.LIVE_DIR, "live", "live")


def sandbox(name):
    if not NAME_RE.match(name or ""):
        raise ValueError(f"Invalid sandbox name: {name!r}")
    root = config.SANDBOX_DIR / name
    if not root.is_dir():
        raise ValueError(f"No sandbox named {name!r}")
    return _load(root, "sandbox", name)


def create_sandbox(name, ai_mode="replay", replace=False, profile=None):
    if not NAME_RE.match(name or ""):
        raise ValueError("Sandbox names use letters, digits, '-', '_' and '.' (max 41 characters).")
    root = config.SANDBOX_DIR / name
    if root.exists():
        if not replace:
            raise ValueError(f"Sandbox {name!r} already exists.")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _meta_path(root).write_text(json.dumps({"ai_mode": ai_mode, "synthetic": True}), encoding="utf-8")
    db.init_db(root / "books.db", profile)
    if profile:
        (root / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    return sandbox(name)


def reset_sandbox(name):
    """Empty a sandbox's books (keep its generated inputs, answer key and mode) and post
    the opening balances again - a clean slate for another evaluation run."""
    import json as _json
    from books.post import post_entry
    biz = sandbox(name)
    for p in (biz.db_path, biz.root / "books.db-journal"):
        if p.exists():
            p.unlink()
    if biz.documents_dir.exists():
        shutil.rmtree(biz.documents_dir)
    db.init_db(biz.db_path, _json.loads((biz.root / "profile.json").read_text(encoding="utf-8"))
               if (biz.root / "profile.json").exists() else None)
    key = _json.loads(biz.answer_key_path.read_text(encoding="utf-8")) if biz.answer_key_path.exists() else {}
    if key.get("opening"):
        with biz.session() as conn:
            post_entry(conn, key["opening"], kind="opening", origin="Test data: sandbox reset (opening balances)")
    return biz


def set_ai_mode(biz, mode):
    if mode not in ("live", "replay"):
        raise ValueError("mode must be live or replay")
    meta = {}
    try:
        meta = json.loads(_meta_path(biz.root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    meta["ai_mode"] = mode
    _meta_path(biz.root).write_text(json.dumps(meta), encoding="utf-8")


def delete_sandbox(name):
    biz = sandbox(name)
    shutil.rmtree(biz.root)
    if active_name() == name:
        set_active("live")


def list_sandboxes():
    if not config.SANDBOX_DIR.is_dir():
        return []
    return sorted(p.name for p in config.SANDBOX_DIR.iterdir() if (p / "books.db").exists())


def active_name():
    try:
        return json.loads(config.ACTIVE_FILE.read_text(encoding="utf-8")).get("name", "live")
    except (OSError, ValueError):
        return "live"


def set_active(name):
    if name != "live":
        sandbox(name)          # validates
    config.ACTIVE_FILE.write_text(json.dumps({"name": name}), encoding="utf-8")


def active():
    name = active_name()
    if name == "live":
        return live()
    try:
        return sandbox(name)
    except ValueError:
        set_active("live")
        return live()


def get(name):
    return live() if name == "live" else sandbox(name)
