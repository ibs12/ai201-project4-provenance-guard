"""Provenance Guard, Flask app.

Milestone 3: the submission endpoint, the first detection signal (Groq LLM), a
structured audit log, and a /log endpoint to read it back. Confidence and the label
here are provisional and built out in Milestones 4 and 5.
"""
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request

import db
from scoring import attribution_from_likelihood, confidence_from_likelihood
from signals import groq_signal

load_dotenv()

app = Flask(__name__)
db.init_db()


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
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = (data.get("creator_id") or "").strip()

    if not text or not creator_id:
        return jsonify({"error": "both 'text' and 'creator_id' are required"}), 400

    content_id = str(uuid.uuid4())
    timestamp = _now()

    # Signal 1: Groq LLM. Returns p_ai (0..1) and a short reason.
    llm = groq_signal(text)
    llm_score = llm["p_ai"]

    # Milestone 3 uses the single LLM signal as a provisional AI likelihood.
    # Milestone 4 replaces this with the weighted mix of both signals.
    ai_likelihood = llm_score
    attribution = attribution_from_likelihood(ai_likelihood)
    confidence = confidence_from_likelihood(ai_likelihood)
    label = f"[provisional] {attribution} - final label text added in Milestone 5"
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
            "stylometry_score": None,
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
            "stylometry_score": None,
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
            "stylometry_score": None,
            "label": label,
            "status": status,
        }
    )


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": db.get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
