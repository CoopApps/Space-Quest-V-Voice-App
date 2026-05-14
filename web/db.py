"""
SQLite layer for the web app.

We reuse the existing sq5_lines.db from voicestudio (the canonical line
catalog), and add a `contributions` table for community uploads.

One line can have many contributions; the owner picks one to be `selected`.
The "compile RESOURCE.AUD" step uses only selected contributions.
"""

import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Path is overridable via env in app.py; this is the default for local dev.
DEFAULT_DB_PATH = Path("D:/projects/SQ5/voicestudio/sq5_lines.db")

# Bundled DB shipped inside the Docker image / repo.  Used to seed the
# persistent volume on first deploy when SQ5_DB_PATH is empty.
BUNDLED_DB_PATH = Path(__file__).resolve().parent.parent / "voicestudio" / "sq5_lines.db"


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


def _has_lines_table(db_path: Path) -> bool:
    if not db_path.exists() or db_path.stat().st_size == 0:
        return False
    con = sqlite3.connect(db_path)
    try:
        row = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lines'"
        ).fetchone()
        return row is not None
    finally:
        con.close()


def init_db(db_path: Path) -> None:
    """
    Make sure `db_path` has both the canonical `lines` table and the
    `contributions` table the web app adds.

    On Railway, SQ5_DB_PATH points into a persistent volume that starts
    empty.  If we detect no `lines` table, we seed the file from the DB
    bundled in the Docker image so the deploy is fully self-contained.
    Idempotent — safe to call on every startup.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if not _has_lines_table(db_path):
        if BUNDLED_DB_PATH.exists():
            shutil.copyfile(BUNDLED_DB_PATH, db_path)
        else:
            # No bundled DB AND no existing one — create an empty file so
            # the contributions table can still be made.  The app will
            # error out on /api/lines calls until somebody seeds it.
            db_path.touch()

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
