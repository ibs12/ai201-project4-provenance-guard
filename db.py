"""SQLite storage for Provenance Guard.

Tables:
- submissions: latest state of each piece of content (for appeal lookups and analytics)
- audit_log: append only record, one row per event (classification or appeal)
- creators: verified human credentials
- challenges: outstanding verification challenges
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
            content_type     TEXT,
            attribution      TEXT,
            ai_likelihood    REAL,
            confidence       REAL,
            llm_score        REAL,
            stylometry_score REAL,
            lexical_score    REAL,
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
            content_type     TEXT,
            attribution      TEXT,
            ai_likelihood    REAL,
            confidence       REAL,
            llm_score        REAL,
            stylometry_score REAL,
            lexical_score    REAL,
            status           TEXT,
            appeal_reasoning TEXT
        );

        CREATE TABLE IF NOT EXISTS creators (
            creator_id     TEXT PRIMARY KEY,
            verified_human INTEGER DEFAULT 0,
            certificate_id TEXT,
            verified_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS challenges (
            challenge_id TEXT PRIMARY KEY,
            creator_id   TEXT,
            prompt       TEXT,
            issued_at    TEXT,
            used         INTEGER DEFAULT 0
        );
        """
    )
    conn.commit()
    conn.close()
    _migrate()


def _migrate():
    """Add columns that older databases might be missing, so an existing
    provenance.db does not break when new fields are introduced."""
    wanted = {
        "submissions": {"content_type": "TEXT", "lexical_score": "REAL"},
        "audit_log": {"content_type": "TEXT", "lexical_score": "REAL"},
    }
    conn = _connect()
    for table, cols in wanted.items():
        existing = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, coltype in cols.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {coltype}")
    conn.commit()
    conn.close()


# --- submissions and audit log --------------------------------------------

def save_submission(row):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO submissions
            (content_id, creator_id, text, content_type, attribution, ai_likelihood,
             confidence, llm_score, stylometry_score, lexical_score, reason, status, created_at)
        VALUES
            (:content_id, :creator_id, :text, :content_type, :attribution, :ai_likelihood,
             :confidence, :llm_score, :stylometry_score, :lexical_score, :reason, :status, :created_at)
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
            (content_id, creator_id, timestamp, event, content_type, attribution,
             ai_likelihood, confidence, llm_score, stylometry_score, lexical_score,
             status, appeal_reasoning)
        VALUES
            (:content_id, :creator_id, :timestamp, :event, :content_type, :attribution,
             :ai_likelihood, :confidence, :llm_score, :stylometry_score, :lexical_score,
             :status, :appeal_reasoning)
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


# --- verified human credentials -------------------------------------------

def grant_credential(creator_id, certificate_id, verified_at):
    conn = _connect()
    conn.execute(
        """
        INSERT INTO creators (creator_id, verified_human, certificate_id, verified_at)
        VALUES (?, 1, ?, ?)
        ON CONFLICT(creator_id) DO UPDATE SET
            verified_human = 1, certificate_id = excluded.certificate_id,
            verified_at = excluded.verified_at
        """,
        (creator_id, certificate_id, verified_at),
    )
    conn.commit()
    conn.close()


def get_credential(creator_id):
    conn = _connect()
    cur = conn.execute("SELECT * FROM creators WHERE creator_id = ?", (creator_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


# --- verification challenges -----------------------------------------------

def save_challenge(challenge_id, creator_id, prompt, issued_at):
    conn = _connect()
    conn.execute(
        "INSERT INTO challenges (challenge_id, creator_id, prompt, issued_at, used) "
        "VALUES (?, ?, ?, ?, 0)",
        (challenge_id, creator_id, prompt, issued_at),
    )
    conn.commit()
    conn.close()


def get_challenge(challenge_id):
    conn = _connect()
    cur = conn.execute("SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def mark_challenge_used(challenge_id):
    conn = _connect()
    conn.execute("UPDATE challenges SET used = 1 WHERE challenge_id = ?", (challenge_id,))
    conn.commit()
    conn.close()


# --- analytics -------------------------------------------------------------

def analytics():
    conn = _connect()

    total = conn.execute("SELECT COUNT(*) AS n FROM submissions").fetchone()["n"]

    by_attr = {r["attribution"]: r["n"] for r in conn.execute(
        "SELECT attribution, COUNT(*) AS n FROM submissions GROUP BY attribution"
    )}

    by_type = {r["content_type"]: r["n"] for r in conn.execute(
        "SELECT content_type, COUNT(*) AS n FROM submissions GROUP BY content_type"
    )}

    appeals = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_log WHERE event = 'appeal'"
    ).fetchone()["n"]

    avgs = conn.execute(
        "SELECT AVG(confidence) AS c, AVG(ai_likelihood) AS a, AVG(llm_score) AS l, "
        "AVG(stylometry_score) AS s, AVG(lexical_score) AS x FROM submissions"
    ).fetchone()

    verified = conn.execute(
        "SELECT COUNT(*) AS n FROM creators WHERE verified_human = 1"
    ).fetchone()["n"]

    conn.close()

    def rnd(v):
        return round(v, 3) if v is not None else None

    return {
        "total_submissions": total,
        "by_attribution": {
            "likely_ai": by_attr.get("likely_ai", 0),
            "uncertain": by_attr.get("uncertain", 0),
            "likely_human": by_attr.get("likely_human", 0),
        },
        "by_content_type": by_type,
        "appeals": appeals,
        "appeal_rate": round(appeals / total, 3) if total else 0.0,
        "avg_confidence": rnd(avgs["c"]),
        "avg_ai_likelihood": rnd(avgs["a"]),
        "avg_signal_scores": {
            "llm": rnd(avgs["l"]),
            "stylometry": rnd(avgs["s"]),
            "lexical": rnd(avgs["x"]),
        },
        "verified_human_creators": verified,
    }
