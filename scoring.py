"""Confidence scoring.

Turns an AI likelihood (0..1) into an attribution bucket and a confidence number.
The buckets use asymmetric thresholds on purpose: it takes stronger evidence (0.70)
to call something AI than to call it human (0.40). On a creative platform, falsely
accusing a real writer is the worst outcome, so the system leans toward not accusing
when the evidence is thin.

The AI likelihood is a weighted vote across the detection signals (combine_signals).
The LLM leads because a holistic read is stronger evidence than surface statistics.
The lexical marker signal is smallest because it is high precision but low recall.
"""

AI_THRESHOLD = 0.70
HUMAN_THRESHOLD = 0.40

# Ensemble weights. Renormalized over whichever signals are present, so dropping
# stylometry for a short caption still produces a valid weighted vote.
WEIGHTS = {"llm": 0.55, "stylometry": 0.25, "lexical": 0.20}


def combine_signals(scores):
    """Weighted vote into one AI likelihood (0..1).

    scores is a dict of signal name -> score (0..1), or None if a signal did not
    apply (for example stylometry on a short caption). Weights are renormalized over
    the signals that are present.
    """
    present = {k: v for k, v in scores.items() if v is not None}
    total_w = sum(WEIGHTS[k] for k in present)
    if total_w == 0:
        return 0.5
    return round(sum(present[k] * WEIGHTS[k] for k in present) / total_w, 3)


def attribution_from_likelihood(ai_likelihood):
    if ai_likelihood >= AI_THRESHOLD:
        return "likely_ai"
    if ai_likelihood <= HUMAN_THRESHOLD:
        return "likely_human"
    return "uncertain"


def confidence_from_likelihood(ai_likelihood):
    """How strongly the system leans toward the side it picked, from 0.5 to 1.0."""
    return round(max(ai_likelihood, 1 - ai_likelihood), 3)
