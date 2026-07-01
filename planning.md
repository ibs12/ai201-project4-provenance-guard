# Planning: Provenance Guard

The spec I wrote before building. I fed pieces of it to an AI tool later when I got to the code.

The system reads a piece of writing and guesses whether its human or AI, and stays honest
about how sure it is instead of forcing a yes or no. One rule drives the rest: calling a
real persons work "AI generated" is the worst mistake it can make, so it needs more
evidence to accuse than it does to clear.

## Architecture

```
SUBMISSION
  POST /submit {text, creator_id, content_type?}
    -> Signal 1 Groq LLM      -> llm_score
    -> Signal 2 stylometry    -> stylometry_score   (text only)
    -> Signal 3 lexical       -> lexical_score
    -> weighted vote -> ai_likelihood -> attribution + confidence
    -> transparency label -> audit log (SQLite) -> response

APPEAL
  POST /appeal {content_id, creator_reasoning}
    -> look up decision (404 if missing) -> status = "under_review"
    -> log appeal next to the original decision -> response
```

On submit the text hits every signal at once, the scores get voted into one likelihood,
that picks a bucket and a label, and the whole thing is logged before the response goes
back. Appeals just flip the status to under_review and log the reasoning next to the
original decision. They dont rerun detection.

## API

| Method | Path | Body | Returns |
|--------|------|------|---------|
| POST | /submit | text, creator_id, content_type? | content_id, attribution, confidence, ai_likelihood, the three signal scores, label, status |
| POST | /appeal | content_id, creator_reasoning | content_id, status, appeal_id, message |
| GET | /log | none | recent audit rows |

`content_id` is a UUID from /submit and the key appeals join on. `attribution` is
`likely_ai`, `uncertain`, or `likely_human`. Stretch endpoints (verify, creator, analytics,
dashboard) are listed in the README.

## Detection signals

Three signals that look at different things: one reads meaning, one measures shape, one
matches known AI phrases. If they always agreed the extra ones would be pointless.

**Signal 1, Groq LLM** (`llama-3.3-70b-versatile`). Reads the passage like a person would
and returns a probability its AI plus a short reason. Catches fluent filler that says
nothing. Blind spot: black box, can be fooled by lightly edited AI, and can read formal or
non native English as too clean.

**Signal 2, stylometry** (pure Python). Three stats, each mapped to a 0 to 1 AI-ness score,
averaged: sentence length variation, type token ratio (moving average so length doesnt
skew it), and punctuation variety. Blind spot: no idea what the text means, unreliable on
short pieces, misreads deliberately uniform human writing as AI.

**Signal 3, lexical markers** (the ensemble stretch). Matches a fixed list of AI tells like
"it is important to note", "a testament to", "delve into", "furthermore". High precision,
low recall, so no markers scores 0 and it gets the smallest weight.

Combine: `ai_likelihood = 0.55*llm + 0.25*stylometry + 0.20*lexical`, renormalized over
whichever signals are present (stylometry is dropped for short captions).

## Uncertainty and confidence

Two numbers on purpose:
- `ai_likelihood` (0 to 1): how likely its AI. The honest core number.
- `confidence` = `max(ai_likelihood, 1 - ai_likelihood)`: how hard it leans. Near 0.5 means
  coin flip, near either end means sure.

Buckets, with the thresholds deliberately lopsided:

```
ai_likelihood >= 0.70  -> likely_ai      (high bar to accuse)
ai_likelihood <= 0.40  -> likely_human    (lower bar, benefit of the doubt)
in between             -> uncertain
```

The gap between 0.70 and 0.40 is the whole false positive rule: it clears a human on
weaker evidence than it accuses one of being AI. A 0.60 lands in uncertain, so the user
sees the uncertain label, not a verdict. (I started at 0.75 but calibration in M4 showed
that let a formal human text tip into likely_ai, so I moved it to 0.70.)

## Transparency label

One per bucket, plain language, never claims certainty. `{confidence_pct}` is filled at
runtime. The uncertain one leaves the number out on purpose.

High confidence AI (`likely_ai`):

> This content shows strong signs of being AI generated. Our system rated it about
> {confidence_pct}% likely to be machine written, based on its writing style and
> language patterns. This is an automated estimate, not a final judgment. If you wrote
> this yourself, you can appeal and a person will take another look.

High confidence human (`likely_human`):

> This content looks human written. Our system found little sign of AI generation and
> rated it about {confidence_pct}% likely to be written by a person. This is an
> automated estimate and not a guarantee of authorship.

Uncertain (`uncertain`):

> We could not tell whether a person or an AI wrote this. The signals were mixed, so we
> are not labeling it either way. Please treat this as inconclusive rather than a verdict.

## Appeals

The creator sends the content_id and their reasoning. The system looks up the decision
(404 if it doesnt exist), sets status to `under_review`, logs an appeal row carrying the
reasoning and the original scores, and confirms with an appeal_id. No auto reclassification,
it just flags the item for a human. No login here, so anyone with the content_id can appeal,
which is a known limitation.

## Edge cases

- **Short pieces** (a haiku, a caption). Stylometry needs length to be stable, so on very
  short text it goes neutral at 0.5 and the LLM leads.
- **Repetitive or simple poetry.** Refrains and plain words look statistically uniform,
  exactly what stylometry reads as AI. The clearest false positive risk, and a main reason
  stylometry is weighted below the LLM.
- **Formal or academic human writing, incl. non native English.** Reads as too clean to the
  LLM and uniform to stylometry. The 0.70 bar catches a lot of these in uncertain instead
  of accusing them, and appeals cover the rest.

## Rate limiting

Flask-Limiter on /submit, 10 per minute and 100 per day per IP. A real writer submits a
handful of pieces in a sitting, so 10/min is well above normal use but stops a script, and
100/day caps abuse from one address. Each submit also calls Groq, so capping bursts protects
the free tier.

## AI Tool Plan

Which spec I hand the tool each milestone and how I check it.

- **M3** (submit + signal 1): give it the signals section and API. Ask for the Flask
  skeleton, the Groq function, the SQLite log, and /log. Check the function returns a 0 to 1
  score, then curl /submit and confirm a row shows in /log.
- **M4** (signal 2 + scoring): give it signals + uncertainty. Ask for the stylometry function
  and the combine using the 0.70 / 0.40 thresholds. Run the four calibration inputs, confirm
  the clear pair splits and the borderline pair sits near uncertain, read the code to check it
  used my thresholds not invented ones.
- **M5** (production): give it the label variants + appeals + rate limiting. Ask for the label
  function, /appeal, and the limiter. Confirm all three labels match this doc, an appeal flips
  status, and >10 requests in a minute return 429.

## Stretch features

All four built. Details and evidence live in the README.

- **Ensemble**: the third signal above (lexical markers) plus the documented 3 way weighted
  vote.
- **Provenance certificate**: /verify/start issues a writing challenge, /verify/complete runs
  the response through detection and grants a "verified human" credential if it reads human.
  Shows on the creators /submit responses and in the label.
- **Analytics dashboard**: /analytics (JSON) and /dashboard (plain HTML) with attribution
  breakdown, appeal rate, average scores, and verified creator count.
- **Multi-modal**: content_type can be `image_caption` as well as `text`. Captions skip
  stylometry (too short) and use a caption specific LLM prompt.
