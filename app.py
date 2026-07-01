"""Provenance Guard, Flask app.

Required endpoints: POST /submit, POST /appeal, GET /log.
Stretch endpoints: POST /verify/start, POST /verify/complete, GET /creator/<id>,
GET /analytics, GET /dashboard.
"""
import random
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
from signals import groq_signal, lexical_signal, stylometry_signal

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

CONTENT_TYPES = ("text", "image_caption")

CHALLENGE_PROMPTS = [
    "Describe a small moment from your day that stuck with you.",
    "Write about a meal you cooked or ate recently, and whether it was any good.",
    "Describe a place you know well, as if telling a friend why you like it.",
    "Write about something that annoyed you this week.",
    "Describe the view from a window you spend time near.",
]


def _now():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def classify(text, content_type="text"):
    """Run the ensemble on a piece of text and return the scored result.

    Stylometry is skipped for image captions, which are too short for its statistics
    to mean anything; the weighted vote renormalizes over the remaining signals.
    """
    llm = groq_signal(text, content_type)
    lexical = lexical_signal(text)
    stylometry_score = stylometry_signal(text)["p_ai"] if content_type == "text" else None

    ai_likelihood = combine_signals(
        {"llm": llm["p_ai"], "stylometry": stylometry_score, "lexical": lexical["p_ai"]}
    )
    return {
        "llm_score": llm["p_ai"],
        "stylometry_score": stylometry_score,
        "lexical_score": lexical["p_ai"],
        "ai_likelihood": ai_likelihood,
        "attribution": attribution_from_likelihood(ai_likelihood),
        "confidence": confidence_from_likelihood(ai_likelihood),
        "reason": llm["reason"],
        "markers": lexical["markers"],
    }


@app.route("/")
def index():
    return jsonify(
        {
            "service": "provenance-guard",
            "endpoints": [
                "POST /submit",
                "POST /appeal",
                "GET /log",
                "POST /verify/start",
                "POST /verify/complete",
                "GET /creator/<creator_id>",
                "GET /analytics",
                "GET /dashboard",
            ],
        }
    )


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    creator_id = (data.get("creator_id") or "").strip()
    content_type = (data.get("content_type") or "text").strip()

    if not text or not creator_id:
        return jsonify({"error": "both 'text' and 'creator_id' are required"}), 400
    if content_type not in CONTENT_TYPES:
        return jsonify({"error": "content_type must be 'text' or 'image_caption'"}), 400

    content_id = str(uuid.uuid4())
    timestamp = _now()

    result = classify(text, content_type)

    credential = db.get_credential(creator_id)
    verified = bool(credential and credential["verified_human"])
    label = make_label(result["attribution"], result["confidence"], verified)
    status = "classified"

    db.save_submission(
        {
            "content_id": content_id,
            "creator_id": creator_id,
            "text": text,
            "content_type": content_type,
            "attribution": result["attribution"],
            "ai_likelihood": result["ai_likelihood"],
            "confidence": result["confidence"],
            "llm_score": result["llm_score"],
            "stylometry_score": result["stylometry_score"],
            "lexical_score": result["lexical_score"],
            "reason": result["reason"],
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
            "content_type": content_type,
            "attribution": result["attribution"],
            "ai_likelihood": result["ai_likelihood"],
            "confidence": result["confidence"],
            "llm_score": result["llm_score"],
            "stylometry_score": result["stylometry_score"],
            "lexical_score": result["lexical_score"],
            "status": status,
            "appeal_reasoning": None,
        }
    )

    return jsonify(
        {
            "content_id": content_id,
            "content_type": content_type,
            "attribution": result["attribution"],
            "confidence": result["confidence"],
            "ai_likelihood": result["ai_likelihood"],
            "llm_score": result["llm_score"],
            "stylometry_score": result["stylometry_score"],
            "lexical_score": result["lexical_score"],
            "label": label,
            "creator_verified": verified,
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
            "content_type": submission["content_type"],
            "attribution": submission["attribution"],
            "ai_likelihood": submission["ai_likelihood"],
            "confidence": submission["confidence"],
            "llm_score": submission["llm_score"],
            "stylometry_score": submission["stylometry_score"],
            "lexical_score": submission["lexical_score"],
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


# --- Stretch: provenance certificate --------------------------------------

@app.route("/verify/start", methods=["POST"])
def verify_start():
    data = request.get_json(silent=True) or {}
    creator_id = (data.get("creator_id") or "").strip()
    if not creator_id:
        return jsonify({"error": "'creator_id' is required"}), 400

    challenge_id = str(uuid.uuid4())
    prompt = random.choice(CHALLENGE_PROMPTS)
    db.save_challenge(challenge_id, creator_id, prompt, _now())
    return jsonify(
        {
            "challenge_id": challenge_id,
            "creator_id": creator_id,
            "prompt": prompt,
            "instructions": "Write 3 to 5 original sentences responding to the prompt, then "
            "post them to /verify/complete with this challenge_id.",
        }
    )


@app.route("/verify/complete", methods=["POST"])
def verify_complete():
    data = request.get_json(silent=True) or {}
    creator_id = (data.get("creator_id") or "").strip()
    challenge_id = (data.get("challenge_id") or "").strip()
    text = (data.get("text") or "").strip()

    if not creator_id or not challenge_id or not text:
        return (
            jsonify({"error": "'creator_id', 'challenge_id', and 'text' are required"}),
            400,
        )

    challenge = db.get_challenge(challenge_id)
    if challenge is None or challenge["creator_id"] != creator_id:
        return jsonify({"error": "unknown challenge for this creator"}), 404
    if challenge["used"]:
        return jsonify({"error": "this challenge has already been used"}), 409

    db.mark_challenge_used(challenge_id)
    result = classify(text, "text")

    # The bar to earn the credential is that the challenge response reads as clearly
    # human. A casual, specific few sentences clears it easily.
    if result["attribution"] == "likely_human":
        certificate_id = str(uuid.uuid4())
        db.grant_credential(creator_id, certificate_id, _now())
        return jsonify(
            {
                "verified": True,
                "creator_id": creator_id,
                "certificate_id": certificate_id,
                "ai_likelihood": result["ai_likelihood"],
                "message": "Verified. This creator now holds a verified human credential.",
            }
        )

    return jsonify(
        {
            "verified": False,
            "creator_id": creator_id,
            "ai_likelihood": result["ai_likelihood"],
            "attribution": result["attribution"],
            "message": "The response did not read as clearly human, so no credential was "
            "granted. You can start a new challenge and try again.",
        }
    )


@app.route("/creator/<creator_id>", methods=["GET"])
def creator(creator_id):
    cred = db.get_credential(creator_id)
    if cred and cred["verified_human"]:
        return jsonify(
            {
                "creator_id": creator_id,
                "verified_human": True,
                "certificate_id": cred["certificate_id"],
                "verified_at": cred["verified_at"],
            }
        )
    return jsonify({"creator_id": creator_id, "verified_human": False})


# --- Stretch: analytics dashboard -----------------------------------------

@app.route("/analytics", methods=["GET"])
def analytics():
    return jsonify(db.analytics())


@app.route("/dashboard", methods=["GET"])
def dashboard():
    return _render_dashboard(db.analytics())


def _bar(label, count, total, color):
    pct = round(100 * count / total) if total else 0
    return (
        f'<div class="row"><span class="lbl">{label}</span>'
        f'<span class="track"><span class="fill" style="width:{pct}%;background:{color}">'
        f'</span></span><span class="num">{count} ({pct}%)</span></div>'
    )


def _render_dashboard(s):
    total = s["total_submissions"]
    attr = s["by_attribution"]
    bars = (
        _bar("Likely AI", attr["likely_ai"], total, "#c0392b")
        + _bar("Uncertain", attr["uncertain"], total, "#b7950b")
        + _bar("Likely human", attr["likely_human"], total, "#1e8449")
    )
    types = "".join(
        f"<li>{k}: {v}</li>" for k, v in sorted(s["by_content_type"].items()) if k
    ) or "<li>none yet</li>"
    avg = s["avg_signal_scores"]
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Provenance Guard dashboard</title>
<style>
  body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto; color: #222; }}
  h1 {{ font-size: 22px; }}
  h2 {{ font-size: 15px; margin-top: 28px; color: #555; text-transform: uppercase; letter-spacing: .04em; }}
  .row {{ display: flex; align-items: center; margin: 8px 0; }}
  .lbl {{ width: 120px; font-size: 14px; }}
  .track {{ flex: 1; background: #eee; border-radius: 4px; height: 18px; overflow: hidden; }}
  .fill {{ display: block; height: 100%; }}
  .num {{ width: 90px; text-align: right; font-size: 13px; color: #555; }}
  .grid {{ display: flex; gap: 24px; flex-wrap: wrap; }}
  .stat {{ background: #f6f6f6; border-radius: 8px; padding: 14px 18px; }}
  .stat .big {{ font-size: 26px; font-weight: 600; }}
  .stat .cap {{ font-size: 12px; color: #666; }}
  ul {{ font-size: 14px; }}
</style></head><body>
<h1>Provenance Guard dashboard</h1>
<div class="grid">
  <div class="stat"><div class="big">{total}</div><div class="cap">submissions</div></div>
  <div class="stat"><div class="big">{round(100 * s["appeal_rate"])}%</div><div class="cap">appeal rate ({s["appeals"]} appeals)</div></div>
  <div class="stat"><div class="big">{s["avg_confidence"] if s["avg_confidence"] is not None else "n/a"}</div><div class="cap">avg confidence</div></div>
  <div class="stat"><div class="big">{s["verified_human_creators"]}</div><div class="cap">verified human creators</div></div>
</div>
<h2>Attribution breakdown</h2>
{bars}
<h2>Average signal scores</h2>
<ul>
  <li>LLM: {avg["llm"]}</li>
  <li>Stylometry: {avg["stylometry"]}</li>
  <li>Lexical: {avg["lexical"]}</li>
</ul>
<h2>By content type</h2>
<ul>{types}</ul>
</body></html>"""


if __name__ == "__main__":
    app.run(debug=True, port=5000)
