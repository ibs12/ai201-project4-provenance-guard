"""Provenance Guard, Flask app.

Milestone 3: the submission endpoint, the first detection signal (Groq LLM), a
structured audit log, and a /log endpoint to read it back. Confidence and the label
here are provisional and built out in Milestones 4 and 5.
"""
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import db
from labels import make_label
from scoring import (
    attribution_from_likelihood,
    combine_signals,
    confidence_from_likelihood,
)
from signals import groq_signal, stylometry_signal

load_dotenv()

app = Flask(__name__)
db.init_db()

# Rate limiting keyed by client IP, in memory (fine for local and grading).
# Limits are documented in the README: a real writer submits a handful of pieces
# in a sitting, so 10/min sits well above normal use while stopping a flood, and
# 100/day caps sustained abuse from one address.
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


def _now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


@app.route("/")
def index():
    return jsonify(
        {
            "service": "provenance-guard",
            "endpoints": ["POST /submit", "POST /appeal", "GET /log"],
        }
    )


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = (data.get("creator_id") or "").strip()

    if not text or not creator_id:
        return jsonify({"error": "both 'text' and 'creator_id' are required"}), 400

    content_id = str(uuid.uuid4())
    timestamp = _now()

    # Signal 1: Groq LLM (semantic). Signal 2: stylometry (structural).
    llm = groq_signal(text)
    llm_score = llm["p_ai"]
    style = stylometry_signal(text)
    stylometry_score = style["p_ai"]

    # AI likelihood is the weighted mix of both signals.
    ai_likelihood = combine_signals(llm_score, stylometry_score)
    attribution = attribution_from_likelihood(ai_likelihood)
    confidence = confidence_from_likelihood(ai_likelihood)
    label = make_label(attribution, confidence)
    status = "classified"

    db.save_submission(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "text": text,
            "attribution": attribution,
            "ai_likelihood": ai_likelihood,
            "confidence": confidence,
            "llm_score": llm_score,
            "stylometry_score": stylometry_score,
            "reason": llm["reason"],
            "status": status,
            "created_at": timestamp,
        }
    )

    db.log_event(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "timestamp": timestamp,
            "event": "classification",
            "attribution": attribution,
            "ai_likelihood": ai_likelihood,
            "confidence": confidence,
            "llm_score": llm_score,
            "stylometry_score": stylometry_score,
            "status": status,
            "appeal_reasoning": None,
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "attribution": attribution,
            "confidence": confidence,
            "ai_likelihood": ai_likelihood,
            "llm_score": llm_score,
            "stylometry_score": stylometry_score,
            "label": label,
            "status": status,
        }
    )


@app.route("/appeal", methods=["POST"])
def appeal():
    data = request.get_json(silent=True) or {}
    content_id = (data.get("content_id") or "").strip()
    creator_reasoning = (data.get("creator_reasoning") or "").strip()

    if not content_id or not creator_reasoning:
        return (
            jsonify({"error": "both 'content_id' and 'creator_reasoning' are required"}),
            400,
        )

    submission = db.get_submission(content_id)
    if submission is None:
        return jsonify({"error": f"no content found with id {content_id}"}), 404

    new_status = "under_review"
    db.update_status(content_id, new_status)

    # Log the appeal next to the original decision so a reviewer sees both together.
    appeal_id = str(uuid.uuid4())
    db.log_event(
        {
            "content_id": content_id,
            "creator_id": submission["creator_id"],
            "timestamp": _now(),
            "event": "appeal",
            "attribution": submission["attribution"],
            "ai_likelihood": submission["ai_likelihood"],
            "confidence": submission["confidence"],
            "llm_score": submission["llm_score"],
            "stylometry_score": submission["stylometry_score"],
            "status": new_status,
            "appeal_reasoning": creator_reasoning,
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "status": new_status,
            "appeal_id": appeal_id,
            "message": "Your appeal was received. This content is now under review by a person.",
        }
    )


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": db.get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
