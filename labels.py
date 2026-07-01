"""Transparency labels shown to a reader on the platform.

One variant per attribution bucket. The AI and human variants show confidence as a
whole percentage. The uncertain variant deliberately leaves the number out, since a
confident figure on an inconclusive result would mislead. The exact wording matches
the label section in planning.md.
"""


def make_label(attribution, confidence):
    pct = round(confidence * 100)

    if attribution == "likely_ai":
        return (
            f"This content shows strong signs of being AI generated. Our system rated it "
            f"about {pct}% likely to be machine written, based on its writing style and "
            f"language patterns. This is an automated estimate, not a final judgment. If "
            f"you wrote this yourself, you can appeal and a person will take another look."
        )

    if attribution == "likely_human":
        return (
            f"This content looks human written. Our system found little sign of AI "
            f"generation and rated it about {pct}% likely to be written by a person. This "
            f"is an automated estimate and not a guarantee of authorship."
        )

    return (
        "We could not tell whether a person or an AI wrote this. The signals were mixed, "
        "so we are not labeling it either way. Please treat this as inconclusive rather "
        "than a verdict."
    )
