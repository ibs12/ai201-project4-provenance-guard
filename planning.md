# Planning: Provenance Guard

The spec I wrote before building. I also feed its sections to an AI tool when I
generate code in the later milestones.

The system takes a piece of writing and decides whether it looks human written or AI
generated, and is honest about how sure it is instead of forcing a yes or no. One rule
drives most of the design: on a creative platform, calling a real person's work "AI
generated" is the worst mistake the system can make, so it needs stronger evidence to
accuse something of being AI than to clear it as human.

## Architecture

```
SUBMISSION
  Client --POST /submit {text, creator_id}--> /submit route
      route --raw text--> Signal 1 (Groq LLM)   --> p_ai_llm   0..1
      route --raw text--> Signal 2 (Stylometry)  --> p_ai_style 0..1
          both scores --> Confidence scoring --> ai_likelihood, confidence, attribution
                          --> Label generator --> label text
                          --> Audit log (SQLite)
      route --> Response {content_id, attribution, confidence, ai_likelihood,
                          llm_score, stylometry_score, label, status}

APPEAL
  Client --POST /appeal {content_id, creator_reasoning}--> /appeal route
      route --lookup content_id--> Storage  (not found -> 404)
          found --> set status = "under_review"
                --> Audit log (appeal + original decision)
      route --> Response {content_id, status: "under_review", appeal_id, message}
```

On submit, the text fans out to both signals at once, their scores combine into one AI
likelihood, that number picks an attribution and a label, and the whole decision is
logged before the response returns. On appeal, the route looks up the stored decision,
flips its status to under review, logs the creator's reasoning next to the original
decision, and confirms. Appeals never rerun detection, they just flag the item.

## API contract

| Method | Path    | Body                              | Returns |
|--------|---------|-----------------------------------|---------|
| POST   | /submit | { text, creator_id }              | content_id, attribution, confidence, ai_likelihood, llm_score, stylometry_score, label, status |
| POST   | /appeal | { content_id, creator_reasoning } | content_id, status ("under_review"), appeal_id, message |
| GET    | /log    | (none)                            | { entries: [ audit rows ] } |

`content_id` is a UUID from /submit and the join key for appeals. `attribution` is one
of `likely_ai`, `uncertain`, `likely_human`. GET /log has no auth here; in production it
would be locked down, but it exists so the log is easy to show for grading.

## Detection signals

Two signals that look at different things. One reads meaning, the other measures shape.
That is what makes combining them worthwhile.

**Signal 1: Groq LLM** (`llama-3.3-70b-versatile`)
- Measures: whether the text reads as human or machine written taken as a whole. Tone,
  specific lived detail, natural imperfection, and whether phrasing feels generic and
  evenly polished.
- Why it differs: models tend to produce smooth, safe, evenly polished prose; human
  writing wanders more and carries personal detail.
- Output: one probability `p_ai_llm` (0 to 1) plus a short reason string for the log,
  returned as strict JSON.
- Blind spot: it is a black box with its own biases, can be fooled by lightly edited AI
  text, and can flag careful or formal human writing (including non native English) as
  too clean. Also non deterministic.

**Signal 2: Stylometry** (pure Python)
- Measures: statistics that differ between uniform machine text and variable human text.
  Three metrics: sentence length variation (coefficient of variation of per sentence word
  counts), type token ratio (unique words over total), and punctuation variety and density.
- Why it differs: AI text is statistically more uniform. Variance, vocabulary spread, and
  punctuation habits show that without understanding meaning.
- Output: each metric maps to a 0 to 1 sub score with a simple linear ramp between two
  anchor points (tuned in M4); `p_ai_style` is their average.
- Blind spot: no idea what the text says, unreliable on short pieces, and misreads
  deliberately uniform human writing (formal prose, repetitive poetry) as AI. That is why
  it is weighted below the LLM.

**Combining:** `combined_p_ai = 0.65 * p_ai_llm + 0.35 * p_ai_style`. The LLM gets more
weight because a holistic read beats surface statistics and stylometry is noisier. Weights
are a starting point checked against the calibration inputs in M4.

## Uncertainty and confidence scoring

The system reports two numbers, and keeping them separate is the point.

- `ai_likelihood` = `combined_p_ai` (0 to 1): how likely the text is AI generated.
- `confidence` = `max(combined_p_ai, 1 - combined_p_ai)` (0.5 to 1.0): how strongly the
  system leans toward the side it picked. Middle likelihood gives confidence near 0.5 (a
  coin flip); either end gives confidence near 1.0.

Attribution buckets use `ai_likelihood` with asymmetric thresholds:

```
ai_likelihood >= 0.70         -> likely_ai      (high bar to accuse of AI)
ai_likelihood <= 0.40         -> likely_human    (lower bar, benefit of the doubt)
0.40 < ai_likelihood < 0.70   -> uncertain
```

The gap between 0.70 and 0.40 is the false positive rule in action: the system will call
something human on weaker evidence than it will call it AI.

A 0.60 likelihood means the signals lean a bit toward AI but do not clear 0.70, so it
lands in the uncertain band and the user sees the uncertain label. The system never
presents a 0.60 as settled.

Testing that scores are meaningful (M4): run four chosen inputs (clearly AI, clearly
human, formal human, lightly edited AI). The clear pair must land in different buckets
with a real gap; the borderline pair should sit in or near uncertain. If one scores
against intuition, print `p_ai_llm` and `p_ai_style` separately to find the culprit
before touching thresholds.

## Transparency label

Three variants, one per bucket, written for a non technical reader, never claiming
certainty. `{confidence_pct}` is confidence as a whole percentage.

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

The uncertain label drops the percentage on purpose, since a confident number on an
inconclusive result sends the wrong message.

## Appeals workflow

- Who: the content's creator. There is no login here, so in practice anyone with the
  content_id can appeal (a known limitation; production would tie it to the authenticated
  creator).
- Provides: `content_id` and `creator_reasoning` (free text).
- System does: look up the decision by content_id (404 if missing), set status to
  `under_review`, write an appeal row to the log carrying the reasoning and pointing back
  at the original decision, and return a confirmation with the appeal_id. No automatic
  re-classification.
- Reviewer view: for each appealed item, the original text, attribution, confidence, both
  signal scores, and the creator's reasoning, pulled from the log. The queue is just the
  log rows where status is `under_review`.

## Audit log

Every decision and appeal is one structured SQLite row, not a print statement. A
classification row:

```json
{
  "content_id": "3f7a2b1e-...",
  "creator_id": "test-user-1",
  "timestamp": "2026-07-01T14:32:10.123Z",
  "event": "classification",
  "attribution": "likely_ai",
  "ai_likelihood": 0.83,
  "confidence": 0.83,
  "llm_score": 0.88,
  "stylometry_score": 0.74,
  "status": "classified",
  "appeal_reasoning": null
}
```

An appeal row reuses the content_id, sets `event` to `appeal`, `status` to `under_review`,
and fills `appeal_reasoning`. GET /log returns recent rows as JSON.

## Rate limiting

Applied to /submit only via Flask-Limiter with in memory storage. Limits: `10 per minute`
and `100 per day` per IP. A real writer checking their own work submits a handful of
pieces in a sitting, so 10 per minute sits well above normal use while stopping a script
that hammers the endpoint, and 100 per day caps sustained abuse from one address while
staying far above any genuine daily need. Limiting bursts also protects the Groq free tier.

## Edge cases

Content this system handles poorly, tied to the signals:

1. **Short submissions** (a haiku, a two sentence caption). Stylometry needs enough words
   and sentences to be stable; on short text, variance is noisy and type token ratio is
   inflated toward 1.0, which swings `p_ai_style`. Plan: lean on the LLM and widen toward
   uncertain rather than commit.
2. **Repetitive or simple poetry.** A refrain, short even lines, and plain vocabulary look
   statistically uniform, exactly the pattern stylometry reads as AI. Low type token ratio
   plus low sentence length variation push `p_ai_style` up on genuine human art. This is
   the clearest false positive risk and a main reason stylometry is weighted below the LLM.
3. **Careful or formal human writing, including non native English.** Textbook phrasing and
   even structure can read as too clean to the LLM and uniform to stylometry. The 0.70
   threshold is meant to catch many of these in the uncertain band rather than accuse them,
   and appeals cover the rest.

## AI Tool Plan

**M3 (submission endpoint + Signal 1).** Provide: Detection signals (Signal 1), the API
contract, the submission diagram. Ask for: Flask skeleton with a POST /submit stub, the
Groq signal function, the SQLite log helper, and GET /log. Verify: call the Groq function
directly on a few strings and confirm it returns a 0 to 1 score, not a string or flag;
then curl /submit and check the response has content_id, attribution, a placeholder
confidence and label, and that a structured row appears in /log.

**M4 (Signal 2 + confidence scoring).** Provide: full Detection signals, Uncertainty and
confidence scoring, the diagram. Ask for: the stylometry function returning `p_ai_style`,
and the scoring function using the exact 0.70 / 0.40 thresholds. Verify: run stylometry
alone on the same inputs and compare with the LLM; run the four calibration inputs and
check the clear pair splits with a real gap and the borderline pair sits near uncertain;
read the scoring code to confirm it uses my thresholds, not invented ones; log both scores.

**M5 (production layer).** Provide: Transparency label, Appeals workflow, Rate limiting,
the diagram. Ask for: the label generator mapping bucket to exact text, the POST /appeal
endpoint, and Flask-Limiter on /submit. Verify: print all three label variants and confirm
they match this document; submit inputs hitting each bucket and confirm the right label;
appeal a real content_id and confirm status flips to under_review and shows in the log;
fire more than 10 requests in a minute and confirm the extra ones return 429.
