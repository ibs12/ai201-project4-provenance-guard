"""Calibration check for the full scoring pipeline (Milestone 4).

Runs four deliberately chosen inputs through both signals and the combined score,
and prints each signal separately so it is clear which one drives the result.

Run:  python test_calibration.py   (needs a real GROQ_API_KEY in .env)
"""
from dotenv import load_dotenv

load_dotenv()

from scoring import attribution_from_likelihood, combine_signals, confidence_from_likelihood
from signals import groq_signal, lexical_signal, stylometry_signal

INPUTS = {
    "clearly AI": (
        "Artificial intelligence represents a transformative paradigm shift in modern "
        "society. It is important to note that while the benefits of AI are numerous, it "
        "is equally essential to consider the ethical implications. Furthermore, "
        "stakeholders across various sectors must collaborate to ensure responsible "
        "deployment."
    ),
    "clearly human": (
        "ok so i finally tried that new ramen place downtown and honestly? underwhelming. "
        "the broth was fine but they put WAY too much sodium in it and i was thirsty for "
        "like three hours after. my friend got the spicy version and said it was better. "
        "probably won't go back unless someone drags me there"
    ),
    "borderline: formal human": (
        "The relationship between monetary policy and asset price inflation has been "
        "extensively studied in the literature. Central banks face a fundamental tension "
        "between their mandate for price stability and the unintended consequences of "
        "prolonged low interest rates on equity and real estate valuations."
    ),
    "borderline: lightly edited AI": (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs, "
        "flexibility and no commute on one side, isolation and blurred work-life boundaries "
        "on the other. Studies show productivity varies widely by individual and role type."
    ),
}


def main():
    print(f"{'input':<30} {'p_llm':>6} {'p_style':>8} {'p_lex':>6} {'combined':>9} {'conf':>6}  attribution")
    print("-" * 84)
    for name, text in INPUTS.items():
        llm = groq_signal(text)
        style = stylometry_signal(text)
        lex = lexical_signal(text)
        combined = combine_signals(
            {"llm": llm["p_ai"], "stylometry": style["p_ai"], "lexical": lex["p_ai"]}
        )
        attribution = attribution_from_likelihood(combined)
        confidence = confidence_from_likelihood(combined)
        print(
            f"{name:<30} {llm['p_ai']:>6} {style['p_ai']:>8} {lex['p_ai']:>6} "
            f"{combined:>9} {confidence:>6}  {attribution}"
        )
        print(
            f"    stylometry -> cv={style['sentence_cv']} (sub {style['cv_sub']}), "
            f"mattr={style['mattr']} (sub {style['mattr_sub']}), "
            f"punct={style['punct_distinct']} (sub {style['punct_sub']})"
        )
        print(f"    lexical markers -> {lex['markers']}")
        print(f"    llm reason -> {llm['reason']}")
        print()


if __name__ == "__main__":
    main()
