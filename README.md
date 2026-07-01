# Provenance Guard

A backend that a creative sharing platform can plug into to check whether a piece of
writing looks human written or AI generated. It does not try to be a perfect detector,
because perfect AI detection is not a solved problem. Instead it combines two independent
signals, reports how confident it is in plain language, and gives creators a way to
appeal when it gets them wrong.

One idea drives the whole design: on a platform for creative work, calling a real
person's writing "AI generated" is the worst mistake the system can make. So it needs
stronger evidence to accuse something of being AI than it does to clear it as human, and
every decision comes with an appeal path.

## How it works (architecture overview)

Here is the path a single submission takes, from the text coming in to the label a
reader sees:

```
POST /submit {text, creator_id}
   |
   v
 raw text fans out to two signals at once
   |-> Signal 1: Groq LLM      -> llm_score        (0..1, semantic read)
   |-> Signal 2: Stylometry    -> stylometry_score (0..1, structural stats)
   |
   v
 combine: ai_likelihood = 0.65*llm_score + 0.35*stylometry_score
   |
   v
 attribution bucket (asymmetric thresholds) + confidence number
   |
   v
 transparency label text chosen from the bucket
   |
   v
 write a structured row to the SQLite audit log
   |
   v
 response {content_id, attribution, confidence, ai_likelihood,
           llm_score, stylometry_score, label, status}
```

The appeal flow is shorter. A creator sends `POST /appeal` with the `content_id` they
got back and their reasoning. The system looks up the original decision, sets its status
to `under_review`, writes an appeal row to the audit log right next to the original
classification, and confirms. Appeals do not rerun detection, they flag the item for a
human. The full diagram for both flows is in [planning.md](planning.md) under Architecture.

## API

| Method | Path    | Body                              | Returns |
|--------|---------|-----------------------------------|---------|
| POST   | /submit | `{ text, creator_id }`            | content_id, attribution, confidence, ai_likelihood, llm_score, stylometry_score, label, status |
| POST   | /appeal | `{ content_id, creator_reasoning }` | content_id, status (`under_review`), appeal_id, message |
| GET    | /log    | none                              | `{ entries: [ audit rows ] }` |

`GET /log` has no auth here. In a real system it would be locked down, but it exists so
the audit log is easy to read for grading.

## Setup and running

```
python -m venv .venv
source .venv/bin/activate          # Mac/Linux
pip install -r requirements.txt
```

Create a `.env` file in the repo root with your Groq key (it is gitignored, never commit
it):

```
GROQ_API_KEY=your_key_here
```

Run the app:

```
python app.py
```

Then submit something:

```
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon, painting the sky in hues of amber and rose.", "creator_id": "test-user-1"}' | python -m json.tool
```

## Detection signals

The system uses two signals that look at genuinely different properties of the text. One
reads meaning, the other measures shape. That is what makes combining them worth more
than either alone: if they always agreed, the second one would be pointless.

### Signal 1: Groq LLM (semantic)

Uses `llama-3.3-70b-versatile`. It reads the passage the way a person would and returns
a single probability that the text is AI generated, plus a short reason string that goes
in the audit log.

- What it measures: tone, specific lived detail, natural imperfection, and whether the
  writing is fluent filler that says very little versus substantive and specific.
- Why I chose it: current models produce smooth, safe, evenly polished prose, and a
  holistic read is the only way to catch that. Pure statistics cannot tell whether a
  sentence actually says anything.
- What it misses: it is a black box with its own biases, it can be fooled by AI text a
  person lightly edited, and it can flag careful or formal human writing as too clean. It
  is also non deterministic, though I run it at temperature 0 to keep it stable.

### Signal 2: Stylometry (structural, pure Python)

Three plain statistics, no external libraries. Each maps to a 0 to 1 AI-ness sub score
with a linear ramp between a human anchor and an AI anchor, and the signal score is their
average.

- Sentence length variation (coefficient of variation of per sentence word counts). Human
  writing mixes short and long sentences, AI text is more even, so low variation leans AI.
- Type token ratio, measured as a moving average over 40 word windows so the number stays
  stable across different text lengths. Very repetitive text leans AI.
- Punctuation variety. Humans reach for dashes, ellipses, parentheses, and question marks,
  AI prose tends to stay plain, so few distinct punctuation marks leans AI.

- Why I chose it: it is cheap, transparent, and completely independent of the LLM. It
  catches statistical uniformity without understanding the text at all.
- What it misses: it has no idea what the text says, it is unreliable on short pieces (so
  the signal stays neutral at 0.5 under 30 words or 2 sentences), and it misreads
  deliberately uniform human writing like formal prose or repetitive poetry as AI. That is
  why it carries less weight than the LLM.

### Combining them

```
ai_likelihood = 0.65 * llm_score + 0.35 * stylometry_score
```

The LLM gets more weight because a holistic read is stronger evidence than surface
statistics, and stylometry is the noisier of the two on real text.

## Confidence scoring

The system reports two numbers on purpose, and keeping them separate is the point.

- `ai_likelihood` (0 to 1): how likely the text is AI generated. This is the honest core
  number.
- `confidence` = `max(ai_likelihood, 1 - ai_likelihood)` (0.5 to 1.0): how strongly the
  system leans toward the side it picked. A likelihood near 0.5 gives confidence near 0.5,
  which is a coin flip. A likelihood near either end gives confidence near 1.0.

The attribution bucket is decided by `ai_likelihood` with asymmetric thresholds:

```
ai_likelihood >= 0.70         -> likely_ai      (high bar to accuse of AI)
ai_likelihood <= 0.40         -> likely_human    (lower bar, benefit of the doubt)
0.40 < ai_likelihood < 0.70   -> uncertain
```

The gap between 0.70 and 0.40 is where the false positive rule lives. The system will
call something human on weaker evidence than it will call it AI. The AI zone is 0.30 wide,
the human zone is 0.40 wide, so a borderline case tilts toward not accusing the writer.

### How I validated that the scores are meaningful

I ran four inputs I chose on purpose (in [test_calibration.py](test_calibration.py)): one
clearly AI, one clearly human, and two borderline cases. The test prints each signal
separately so I can see which one drives the result. Final scores:

| input | llm_score | stylometry_score | ai_likelihood | bucket |
|---|---|---|---|---|
| clearly AI essay | 0.80 | 0.627 | 0.739 | likely_ai |
| clearly human (casual review) | 0.20 | 0.196 | 0.199 | likely_human |
| borderline: formal human (economics) | 0.60 | 0.744 | 0.650 | uncertain |
| borderline: lightly edited AI | 0.60 | 0.403 | 0.531 | uncertain |

This is what "meaningful" means to me: the clear cases sit 0.54 apart, all three buckets
are reachable, and both borderline cases land in the middle instead of getting a confident
verdict. The formal economics text is the important one. Both signals actually lean AI on
it (stylometry 0.744, because formal academic writing really is uniform), but it still
lands in `uncertain` rather than `likely_ai`, which is exactly the false positive the
system is built to avoid.

### Two example submissions

High confidence result:

```
text:       "ok so i finally tried that new ramen place downtown and honestly?
             underwhelming. the broth was fine but they put WAY too much sodium..."
llm_score:        0.2
stylometry_score: 0.196
ai_likelihood:    0.199
confidence:       0.801
attribution:      likely_human
```

Lower confidence result:

```
text:       "I've been thinking a lot about remote work lately. There are genuine
             tradeoffs, flexibility and no commute on one side, isolation and blurred
             work-life boundaries on the other. Studies show productivity varies..."
llm_score:        0.6
stylometry_score: 0.403
ai_likelihood:    0.531
confidence:       0.531
attribution:      uncertain
```

The first is a confident human call at 0.801. The second is a lightly edited AI passage
that sits almost on the fence at 0.531, so the system says it cannot tell rather than
guessing. Same pipeline, very different confidence, which is the whole idea.

## Transparency label

The label is what a reader sees on the platform. There is one variant per bucket. It is
written for a non technical reader and never claims certainty. The AI and human variants
show confidence as a whole percentage. The uncertain variant leaves the number out on
purpose, because a confident figure on an inconclusive result would send the wrong message.

**High confidence AI** (bucket `likely_ai`):

> This content shows strong signs of being AI generated. Our system rated it about
> {confidence}% likely to be machine written, based on its writing style and language
> patterns. This is an automated estimate, not a final judgment. If you wrote this
> yourself, you can appeal and a person will take another look.

**High confidence human** (bucket `likely_human`):

> This content looks human written. Our system found little sign of AI generation and
> rated it about {confidence}% likely to be written by a person. This is an automated
> estimate and not a guarantee of authorship.

**Uncertain** (bucket `uncertain`):

> We could not tell whether a person or an AI wrote this. The signals were mixed, so we
> are not labeling it either way. Please treat this as inconclusive rather than a verdict.

`{confidence}` is filled in at runtime. For example the clearly human ramen review above
renders as "rated it about 80% likely to be written by a person," and the clearly AI essay
renders as "about 74% likely to be machine written."

## Appeals workflow

A creator who thinks the classification is wrong sends `POST /appeal` with the
`content_id` from their submission and a `creator_reasoning` string. The system:

1. Looks up the original decision by content_id (returns 404 if it does not exist).
2. Sets that content's status to `under_review`.
3. Writes an appeal row to the audit log carrying the reasoning and pointing back at the
   original decision (its attribution, confidence, and both signal scores).
4. Returns a confirmation with an appeal_id and the new status.

There is no automatic re-classification. An appeal flags the item for a human, it does not
rerun detection. A reviewer opening the queue (the log rows where status is `under_review`)
sees the original text's decision and the creator's reasoning side by side.

```
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "PASTE-ID-HERE", "creator_reasoning": "I wrote this myself..."}' | python -m json.tool
```

There is no login in this project, so in practice anyone with the content_id can appeal.
In production the appeal would be tied to the authenticated creator. This is a known
limitation, noted below.

## Rate limiting

`POST /submit` is rate limited with Flask-Limiter, keyed by client IP, using in memory
storage.

- **Limits: 10 per minute and 100 per day.**
- Reasoning: a real writer checking their own work submits a handful of pieces in a
  sitting, not ten in a minute, so 10 per minute sits comfortably above normal human use
  while stopping a script that tries to flood the endpoint. The 100 per day cap puts a
  ceiling on sustained abuse from one address across a whole day while staying far above
  anything a genuine single creator would need. Every submission also makes a Groq API
  call, so limiting bursts protects the free tier quota too.

Evidence, sending 12 rapid requests (the first 10 pass, the rest are rejected):

```
status codes: [200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 429, 429]
200 count: 10   429 count: 2
```

Reproduce against a running server with the loop from the project brief, or run the
included check in a fresh process.

## Audit log

Every classification and every appeal is written as one structured row in SQLite (see
[db.py](db.py)), not a print statement. `GET /log` returns the most recent rows as JSON.
Below is a real sample: three submissions spanning all three buckets, plus an appeal that
econ_lena filed on her formal economics writing after it landed in `uncertain`. The rows
come back newest first, so the appeal (id 4) is on top, next to the original classification
it points at (id 3).

```json
{
  "entries": [
    {
      "id": 4,
      "event": "appeal",
      "content_id": "b61da1cd-3146-4911-b342-f86c43f2dd49",
      "creator_id": "econ_lena",
      "timestamp": "2026-07-01T05:56:47.701Z",
      "attribution": "uncertain",
      "ai_likelihood": 0.65,
      "confidence": 0.65,
      "llm_score": 0.6,
      "stylometry_score": 0.744,
      "status": "under_review",
      "appeal_reasoning": "This is my own analysis for a graduate economics seminar. English is my second language and my academic style reads as formal."
    },
    {
      "id": 3,
      "event": "classification",
      "content_id": "b61da1cd-3146-4911-b342-f86c43f2dd49",
      "creator_id": "econ_lena",
      "timestamp": "2026-07-01T05:56:47.417Z",
      "attribution": "uncertain",
      "ai_likelihood": 0.65,
      "confidence": 0.65,
      "llm_score": 0.6,
      "stylometry_score": 0.744,
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 2,
      "event": "classification",
      "content_id": "98b9fd51-f966-482d-a12f-b27dc04a46a0",
      "creator_id": "writer_jon",
      "timestamp": "2026-07-01T05:56:47.137Z",
      "attribution": "likely_human",
      "ai_likelihood": 0.199,
      "confidence": 0.801,
      "llm_score": 0.2,
      "stylometry_score": 0.196,
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 1,
      "event": "classification",
      "content_id": "43800d0f-29d6-41de-9351-2592f93c6d9a",
      "creator_id": "poet_maya",
      "timestamp": "2026-07-01T05:56:46.432Z",
      "attribution": "likely_ai",
      "ai_likelihood": 0.739,
      "confidence": 0.739,
      "llm_score": 0.8,
      "stylometry_score": 0.627,
      "status": "classified",
      "appeal_reasoning": null
    }
  ]
}
```

Each row carries the timestamp, content id, creator, attribution, confidence, both
individual signal scores, the combined likelihood, the status, and the appeal reasoning
when there is one.

## Known limitations

The system will get some real content wrong, and the failures are tied to specific
properties of the signals.

- **Repetitive or simple poetry.** A poem built on a refrain, short even lines, and plain
  vocabulary looks statistically uniform, which is exactly the pattern the stylometry
  signal reads as AI. Low type token ratio from the repetition and low sentence length
  variation both push the stylometry score up, even though the piece is genuine human art.
  This is the clearest false positive risk in the system, and it is a direct consequence of
  stylometry measuring shape without meaning. It is a big reason stylometry is weighted
  below the LLM and why the AI threshold is set conservatively.

- **Formal or academic human writing, including from non native English speakers.** The
  economics example in the calibration table is real: both signals lean AI on it because
  careful, even, textbook style prose reads as too clean to the LLM and as uniform to
  stylometry. The 0.70 threshold catches many of these in the uncertain band instead of
  accusing them, and the appeal path exists for the rest, but the system genuinely cannot
  distinguish polished human prose from AI with confidence. That is an honest limit, not a
  bug I can tune away.

- **No auth on appeals.** Anyone with a content_id can file an appeal or read the log.
  Fine for this project, not for production.

## Spec reflection

**One way the spec helped.** Writing the three label variants and the confidence design
in planning.md before any code kept me from building a binary detector. Deciding up front
that the system would report two separate numbers (`ai_likelihood` and `confidence`) and
that thresholds would be asymmetric meant the scoring and label code was a direct
translation of the spec instead of something I reverse engineered after the fact. When I
got to the label function, the text was already written and agreed on.

**One way the implementation diverged.** My planning.md set the AI threshold at 0.75 and
guessed the type token ratio anchors at 0.72 and 0.45. Calibration in Milestone 4 proved
both wrong. The formal economics text false positived into `likely_ai`, and the clearly AI
essay undershot into `uncertain`. Investigating showed two problems: the type token anchors
were far below the metric's real range, so every normal text pinned that metric at 0 and it
never contributed, and the LLM was treating formality itself as an AI tell. I fixed the
anchors to the real range (0.65 to 0.95), rewrote the LLM prompt to weigh substance over
polish, and lowered the threshold to 0.70. Then all four inputs landed correctly. I updated
planning.md to match, so the spec and the code agree.

## AI usage

I used an AI coding tool throughout, driven by the sections of planning.md. Two specific
instances where I had to revise or override what it produced:

1. **The LLM detection prompt.** I directed the tool to write the Groq signal function
   from my detection signals spec. Its first prompt asked the model to judge how "polished"
   and "evenly written" the text was. When I tested it, the formal economics passage scored
   0.8, the same as an obvious AI essay, which would have produced a false positive on real
   human writing. I overrode the prompt and rewrote it around a different principle: fluent
   filler that says little is the AI tell, while specific, substantive, checkable claims
   lean human even in a formal register. That dropped the economics text to 0.6 without
   moving the real AI case.

2. **The stylometry scoring anchors.** I asked the tool to generate the stylometry function
   and the code that maps each metric to a 0 to 1 score. The mapping it produced looked
   reasonable but the type token ratio anchors were set so that any normal length text
   scored 0 on that metric, so it silently contributed nothing and dragged AI scores down.
   I caught this by printing the raw metric values during calibration, then corrected the
   anchors to the metric's actual range and re-tested. This is the kind of thing the brief
   warned about: AI generated scoring that looks fine but quietly diverges from what the
   numbers should be.

## Project structure

```
app.py               Flask app: /submit, /appeal, /log, rate limiting
signals.py           Signal 1 (Groq LLM) and Signal 2 (stylometry)
scoring.py           combine_signals, attribution buckets, confidence
labels.py            the three transparency label variants
db.py                SQLite storage: submissions table + audit_log
test_signal.py       manual check of Signal 1 alone
test_calibration.py  four input calibration of the full pipeline
planning.md          the spec, written before the code
requirements.txt
```
