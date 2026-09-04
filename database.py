"""SQLite schema and connection helper for AI Study Assistant."""

import os
import json
import sqlite3

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "study_assistant.db")

# How long a connection waits for a lock before raising "database is
# locked", in milliseconds. app.py runs Flask with threaded=True, so
# concurrent requests (e.g. a quiz-generation call overlapping a chat
# message on the same document) can genuinely contend for the DB --
# without this, SQLite's default is to fail immediately instead of
# waiting briefly for the other write to finish.
BUSY_TIMEOUT_MS = int(os.environ.get("DB_BUSY_TIMEOUT_MS", "5000"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name  TEXT NOT NULL,
    stored_name    TEXT NOT NULL,
    file_type      TEXT NOT NULL,
    extracted_text TEXT NOT NULL,
    summary        TEXT,
    word_count     INTEGER NOT NULL,
    uploaded_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quizzes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    questions    TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    quiz_id      INTEGER NOT NULL REFERENCES quizzes(id) ON DELETE CASCADE,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    results      TEXT NOT NULL,
    score        INTEGER NOT NULL,
    total        INTEGER NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS flashcard_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    cards        TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
"""


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
    # WAL lets readers and a writer proceed concurrently instead of
    # blocking each other, which matters here for the same reason as
    # BUSY_TIMEOUT_MS above.
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db():
    os.makedirs(BASE_DIR, exist_ok=True)
    with get_connection() as conn:
        conn.executescript(SCHEMA)


def document_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    return d


def quiz_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    d["questions"] = json.loads(d["questions"])
    return d


def flashcards_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    d["cards"] = json.loads(d["cards"])
    return d


def attempt_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    d["results"] = json.loads(d["results"])
    return d
