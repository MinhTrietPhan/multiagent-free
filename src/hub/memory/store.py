"""Durable local conversation and explicit memory storage (SQLite + Markdown)."""
from __future__ import annotations
import os
import sqlite3
from pathlib import Path


def data_dir() -> Path:
    path = Path(os.getenv("HUB_DATA_DIR", Path.home() / ".agent-hub")).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


class MemoryStore:
    def __init__(self, db_path: str | Path | None = None):
        self.db_path = Path(db_path).expanduser() if db_path else data_dir() / "hub.sqlite3"
        self.directory = self.db_path.parent
        self.directory.mkdir(parents=True, exist_ok=True)
        self.user_memory_path = self.directory / "user_memory.md"
        self._init_db()

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, thread_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, agent TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id)")
            db.execute("CREATE TABLE IF NOT EXISTS thread_state (thread_id TEXT PRIMARY KEY, active_project_root TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP)")

    def set_active_project(self, thread_id: str, root: str | Path | None):
        """Remember the project a thread is working on (survives a CLI restart)."""
        with self._connect() as db:
            if root:
                db.execute(
                    "INSERT INTO thread_state(thread_id, active_project_root) VALUES (?, ?) "
                    "ON CONFLICT(thread_id) DO UPDATE SET active_project_root=excluded.active_project_root, updated_at=CURRENT_TIMESTAMP",
                    (thread_id, str(root)),
                )
            else:
                db.execute("DELETE FROM thread_state WHERE thread_id=?", (thread_id,))

    def get_active_project(self, thread_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT active_project_root FROM thread_state WHERE thread_id=?", (thread_id,)
            ).fetchone()
        return row[0] if row and row[0] else None

    def save_turn(self, thread_id: str, user_text: str, assistant_text: str, agent: str | None = None):
        with self._connect() as db:
            db.executemany("INSERT INTO messages(thread_id, role, content, agent) VALUES (?, ?, ?, ?)", [
                (thread_id, "user", user_text, None), (thread_id, "assistant", assistant_text, agent)
            ])

    def load_history(self, thread_id: str, limit: int = 40) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT role, content, agent FROM (SELECT id, role, content, agent FROM messages WHERE thread_id=? ORDER BY id DESC LIMIT ?) ORDER BY id", (thread_id, limit)).fetchall()
        return [{"role": r[0], "content": r[1], "agent": r[2]} for r in rows]

    def clear_thread(self, thread_id: str):
        with self._connect() as db:
            db.execute("DELETE FROM messages WHERE thread_id=?", (thread_id,))

    def list_threads(self, limit: int = 20) -> list[tuple[str, str, int]]:
        with self._connect() as db:
            return db.execute("SELECT thread_id, MAX(created_at), COUNT(*) FROM messages GROUP BY thread_id ORDER BY MAX(id) DESC LIMIT ?", (limit,)).fetchall()

    def read_user_memory(self) -> str:
        return self.user_memory_path.read_text(encoding="utf-8") if self.user_memory_path.exists() else ""

    def append_user_memory(self, note: str):
        note = note.strip()
        if not note:
            raise ValueError("Memory note cannot be empty")
        with self.user_memory_path.open("a", encoding="utf-8") as f:
            f.write(("\n" if self.user_memory_path.exists() else "") + f"- {note}\n")

    @staticmethod
    def read_project_memory(root: str | Path = ".") -> str:
        p = Path(root) / ".hub" / "project_memory.md"
        return p.read_text(encoding="utf-8") if p.is_file() else ""

    @staticmethod
    def append_project_memory(root: str | Path, note: str) -> Path:
        """Append one durable fact to <project>/.hub/project_memory.md (read by QA every turn)."""
        note = note.strip()
        if not note:
            raise ValueError("Memory note cannot be empty")
        path = Path(root) / ".hub" / "project_memory.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(("\n" if path.stat().st_size else "") + f"- {note}\n")
        return path

