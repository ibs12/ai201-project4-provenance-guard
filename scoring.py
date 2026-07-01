"""Confidence scoring.

Turns an AI likelihood (0..1) into an attribution bucket and a confidence number.
The buckets use asymmetric thresholds on purpose: it takes stronger evidence (0.75)
to call something AI than to call it human (0.40). On a creative platform, falsely
accusing a real writer is the worst outcome, so the system leans toward not
accusing when the evidence is thin.

The AI likelihood itself is the weighted mix of the two signals (combine_signals).
The LLM carries more weight because a holistic read is stronger evidence than
surface statistics, and stylometry is the noisier of the two on real text.
"""

AI_THRESHOLD = 0.70
HUMAN_THRESHOLD = 0.40

LLM_WEIGHT = 0.65
STYLOMETRY_WEIGHT = 0.35


def combine_signals(p_llm, p_style):
    """Weighted mix of the two signal scores into one AI likelihood (0..1)."""
    return round(LLM_WEIGHT * p_llm + STYLOMETRY_WEIGHT * p_style, 3)


def attribution_from_likelihood(ai_likelihood):
    if ai_likelihood >= AI_THRESHOLD:
        return "likely_ai"
    if ai_likelihood <= HUMAN_THRESHOLD:
        return "likely_human"
    return "uncertain"


def confidence_from_likelihood(ai_likelihood):
    """How strongly the system leans toward the side it picked, from 0.5 to 1.0."""
    return round(max(ai_likelihood, 1 - ai_likelihood), 3)
