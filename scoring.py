"""Confidence scoring.

Turns an AI likelihood (0..1) into an attribution bucket and a confidence number.
The buckets use asymmetric thresholds on purpose: it takes stronger evidence (0.75)
to call something AI than to call it human (0.40). On a creative platform, falsely
accusing a real writer is the worst outcome, so the system leans toward not
accusing when the evidence is thin.

Milestone 3 feeds this the single LLM score as a provisional likelihood. Milestone 4
adds combine_signals() so the likelihood is the weighted mix of both signals.
"""

AI_THRESHOLD = 0.75
HUMAN_THRESHOLD = 0.40


def attribution_from_likelihood(ai_likelihood):
    if ai_likelihood >= AI_THRESHOLD:
        return "likely_ai"
    if ai_likelihood <= HUMAN_THRESHOLD:
        return "likely_human"
    return "uncertain"


def confidence_from_likelihood(ai_likelihood):
    """How strongly the system leans toward the side it picked, from 0.5 to 1.0."""
    return round(max(ai_likelihood, 1 - ai_likelihood), 3)
