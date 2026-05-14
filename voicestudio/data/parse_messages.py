"""
Parse SQ5 .msg-msg.txt files into a SQLite database.

Column format (tab-separated, 6 cols):
  noun | verb | cond | seq | talker | text

The module (room number) comes from the filename: NNN.msg-msg.txt
"""

import os
import re
import json
import sqlite3
import argparse
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS lines (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    module        INTEGER NOT NULL,
    noun          INTEGER NOT NULL,
    verb          INTEGER NOT NULL,
    cond          INTEGER NOT NULL,
    seq           INTEGER NOT NULL,
    talker_id     INTEGER NOT NULL,
    character_name TEXT,
    text          TEXT NOT NULL,
    recorded      INTEGER NOT NULL DEFAULT 0,
    wav_path      TEXT,
    sol_path      TEXT,
    UNIQUE(module, noun, verb, cond, seq)
);

CREATE INDEX IF NOT EXISTS idx_talker   ON lines(talker_id);
CREATE INDEX IF NOT EXISTS idx_module   ON lines(module);
CREATE INDEX IF NOT EXISTS idx_recorded ON lines(recorded);
"""


def load_characters(characters_json: Path) -> dict[str, str]:
    if characters_json.exists():
        with open(characters_json, encoding="utf-8") as f:
            return json.load(f)
    return {}


def parse_msg_file(path: Path) -> list[dict]:
    module = int(path.name.split(".")[0])
    rows = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 6:
                continue
            noun_s, verb_s, cond_s, seq_s, talker_s, text = parts
            try:
                noun   = int(noun_s.strip())
                verb   = int(verb_s.strip())
                cond   = int(cond_s.strip())
                seq    = int(seq_s.strip())
                talker = int(talker_s.strip())
            except ValueError:
                continue
            text = text.strip()
            if not text:
                continue
            rows.append({
                "module": module,
                "noun":   noun,
                "verb":   verb,
                "cond":   cond,
                "seq":    seq,
                "talker": talker,
                "text":   text,
            })
    return rows


def build_database(sqdis_dir: Path, db_path: Path, characters_json: Path) -> None:
    characters = load_characters(characters_json)
    con = sqlite3.connect(db_path)
    con.executescript(SCHEMA)

    msg_files = sorted(
        p for p in sqdis_dir.iterdir()
        if p.name.endswith(".msg-msg.txt")
    )

    inserted = skipped = 0
    for msg_file in msg_files:
        rows = parse_msg_file(msg_file)
        for r in rows:
            char_name = characters.get(str(r["talker"]))
            try:
                con.execute(
                    """INSERT INTO lines
                       (module, noun, verb, cond, seq, talker_id, character_name, text)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (r["module"], r["noun"], r["verb"], r["cond"], r["seq"],
                     r["talker"], char_name, r["text"]),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                skipped += 1

    con.commit()
    con.close()
    print(f"Inserted {inserted} lines ({skipped} duplicates skipped) into {db_path}")


def print_summary(db_path: Path) -> None:
    con = sqlite3.connect(db_path)
    print("\n=== Database summary ===")
    total, = con.execute("SELECT COUNT(*) FROM lines").fetchone()
    print(f"Total lines: {total}")

    print("\nLines per character (voiced only):")
    rows = con.execute("""
        SELECT COALESCE(character_name, 'Unknown-' || talker_id), COUNT(*)
        FROM lines
        WHERE talker_id NOT IN (97, 98, 99)
        GROUP BY talker_id
        ORDER BY COUNT(*) DESC
    """).fetchall()
    for name, count in rows:
        print(f"  {name:<25} {count}")

    narrator, = con.execute(
        "SELECT COUNT(*) FROM lines WHERE talker_id = 99"
    ).fetchone()
    print(f"\nNarrator/description lines: {narrator}")

    modules, = con.execute("SELECT COUNT(DISTINCT module) FROM lines").fetchone()
    print(f"Rooms with dialogue: {modules}")
    con.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse SQ5 message files into SQLite")
    parser.add_argument("--sqdis",      default="D:/projects/sq5/sqdis",
                        help="Path to sqdis directory")
    parser.add_argument("--db",         default="D:/projects/SQ5/voicestudio/sq5_lines.db",
                        help="Output SQLite database path")
    parser.add_argument("--characters", default="D:/projects/SQ5/voicestudio/data/characters.json",
                        help="Path to characters.json")
    args = parser.parse_args()

    build_database(
        Path(args.sqdis),
        Path(args.db),
        Path(args.characters),
    )
    print_summary(Path(args.db))
