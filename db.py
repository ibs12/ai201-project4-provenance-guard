"""SQLite storage for Provenance Guard.

Two tables. 'submissions' holds the latest state of each piece of content so the
appeal endpoint can look it up by content_id and flip its status. 'audit_log' is
the append only record: one row per event (a classification or an appeal), which
is what GET /log reads back.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "provenance.db")


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = _connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS submissions (
            content_id       TEXT PRIMARY KEY,
            creator_id       TEXT,
            text             TEXT,
            attribution      TEXT,
            ai_likelihood    REAL,
            confidence       REAL,
            llm_score        REAL,
            stylometry_score REAL,
            reason           TEXT,
            status           TEXT,
            created_at       TEXT
        );

        CREATE TABLE IF NOT EXISTS audit_log (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            content_id       TEXT,
            creator_id       TEXT,
            timestamp        TEXT,
            event            TEXT,
            attribution      TEXT,
            ai_likelihood    REAL,
            confidence       REAL,
            llm_score        REAL,
            stylometry_score REAL,
            status           TEXT,
            appeal_reasoning TEXT
        );
        """
    )
    conn.commit()
    conn.close()


def save_submission(row):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO submissions
            (content_id, creator_id, text, attribution, ai_likelihood,
             confidence, llm_score, stylometry_score, reason, status, created_at)
        VALUES
            (:content_id, :creator_id, :text, :attribution, :ai_likelihood,
             :confidence, :llm_score, :stylometry_score, :reason, :status, :created_at)
        """,
        row,
    )
    conn.commit()
    conn.close()


def get_submission(content_id):
    conn = _connect()
    cur = conn.execute("SELECT * FROM submissions WHERE content_id = ?", (content_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_status(content_id, status):
    conn = _connect()
    conn.execute(
        "UPDATE submissions SET status = ? WHERE content_id = ?", (status, content_id)
    )
    conn.commit()
    conn.close()


def log_event(row):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO audit_log
            (content_id, creator_id, timestamp, event, attribution, ai_likelihood,
             confidence, llm_score, stylometry_score, status, appeal_reasoning)
        VALUES
            (:content_id, :creator_id, :timestamp, :event, :attribution, :ai_likelihood,
             :confidence, :llm_score, :stylometry_score, :status, :appeal_reasoning)
        """,
        row,
    )
    conn.commit()
    conn.close()


def get_log(limit=50):
    conn = _connect()
    cur = conn.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows
