"""
SQLite layer for the web app.

We reuse the existing sq5_lines.db from voicestudio (the canonical line
catalog), and add a `contributions` table for community uploads.

One line can have many contributions; the owner picks one to be `selected`.
The "compile RESOURCE.AUD" step uses only selected contributions.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Path is overridable via env in app.py; this is the default for local dev.
DEFAULT_DB_PATH = Path("D:/projects/SQ5/voicestudio/sq5_lines.db")


SCHEMA_EXTENSIONS = """
CREATE TABLE IF NOT EXISTS contributions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    module          INTEGER NOT NULL,
    noun            INTEGER NOT NULL,
    verb            INTEGER NOT NULL,
    cond            INTEGER NOT NULL,
    seq             INTEGER NOT NULL,
    contributor     TEXT NOT NULL DEFAULT 'anonymous',
    filename        TEXT NOT NULL,
    duration_sec    REAL,
    uploaded_at     TEXT NOT NULL DEFAULT (datetime('now')),
    selected        INTEGER NOT NULL DEFAULT 0,
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_contrib_line
    ON contributions (module, noun, verb, cond, seq);

CREATE INDEX IF NOT EXISTS idx_contrib_selected
    ON contributions (module, noun, verb, cond, seq, selected);
"""


def init_db(db_path: Path) -> None:
    """Create the contributions table if it doesn't exist. Idempotent."""
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA_EXTENSIONS)
        con.commit()
    finally:
        con.close()


@contextmanager
def open_db(db_path: Path) -> Iterator[sqlite3.Connection]:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()
