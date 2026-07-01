# Provenance Guard

A backend that a creative sharing platform can plug into to check whether a piece of
writing looks human written or AI generated. It does not try to be a perfect detector,
because perfect AI detection is not a solved problem. Instead it runs an ensemble of three
independent signals, reports how confident it is in plain language, and gives creators a
way to appeal when it gets them wrong.

One idea drives the whole design: on a platform for creative work, calling a real
person's writing "AI generated" is the worst mistake the system can make. So it needs
stronger evidence to accuse something of being AI than it does to clear it as human, and
every decision comes with an appeal path.

All four stretch features are also built: a third detection signal (ensemble), a verified
human credential, an analytics dashboard, and multi-modal support for image captions. They
are documented in their own section below.

## How it works (architecture overview)

Here is the path a single submission takes, from the text coming in to the label a
reader sees:

```
POST /submit {text, creator_id, content_type?}
   |
   v
 raw text fans out to the signals at once
   |-> Signal 1: Groq LLM       -> llm_score        (0..1, semantic read)
   |-> Signal 2: Stylometry     -> stylometry_score (0..1, structural stats)  [text only]
   |-> Signal 3: Lexical markers -> lexical_score    (0..1, known AI phrases)
   |
   v
 weighted vote: ai_likelihood = 0.55*llm + 0.25*stylometry + 0.20*lexical
   (renormalized over whichever signals are present)
   |
   v
 attribution bucket (asymmetric thresholds) + confidence number
   |
   v
 transparency label text (plus a credential line if the creator is verified)
   |
   v
 write a structured row to the SQLite audit log
   |
   v
 response {content_id, content_type, attribution, confidence, ai_likelihood,
           llm_score, stylometry_score, lexical_score, label, creator_verified, status}
```

The appeal flow is shorter. A creator sends `POST /appeal` with the `content_id` they
got back and their reasoning. The system looks up the original decision, sets its status
to `under_review`, writes an appeal row to the audit log right next to the original
classification, and confirms. Appeals do not rerun detection, they flag the item for a
human. The full diagram for both flows is in [planning.md](planning.md) under Architecture.

## API

Required endpoints:

| Method | Path    | Body                              | Returns |
|--------|---------|-----------------------------------|---------|
| POST   | /submit | `{ text, creator_id, content_type? }` | content_id, content_type, attribution, confidence, ai_likelihood, llm_score, stylometry_score, lexical_score, label, creator_verified, status |
| POST   | /appeal | `{ content_id, creator_reasoning }` | content_id, status (`under_review`), appeal_id, message |
| GET    | /log    | none                              | `{ entries: [ audit rows ] }` |

Stretch endpoints:

| Method | Path                    | Body / param                          | Returns |
|--------|-------------------------|---------------------------------------|---------|
| POST   | /verify/start           | `{ creator_id }`                      | challenge_id, prompt, instructions |
| POST   | /verify/complete        | `{ creator_id, challenge_id, text }`  | verified, certificate_id (if granted), ai_likelihood, message |
| GET    | /creator/&lt;creator_id&gt;  | none                             | creator_id, verified_human, certificate_id |
| GET    | /analytics              | none                                  | detection patterns, appeal rate, verified creator count |
| GET    | /dashboard              | none                                  | HTML dashboard view |

`content_type` is optional and defaults to `text`. The other value is `image_caption`.
`GET /log`, `/analytics`, and `/dashboard` have no auth here. In a real system they would
be locked down, but they exist so the audit log and stats are easy to read for grading.

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

The system uses three signals that look at genuinely different properties of the text.
One reads meaning, one measures shape, one matches a fixed vocabulary of known tells.
That is what makes combining them worth more than any one alone: if they always agreed,
the extra ones would be pointless.

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
  deliberately uniform human writing like formal prose or repetitive poetry as AI.

### Signal 3: Lexical AI markers (added as the ensemble stretch)

A fixed vocabulary of phrases and words that show up far more often in AI writing than in
casual human writing, like "it is important to note", "a testament to", "delve into",
"furthermore", and "studies show". It counts how many distinct markers appear and maps
that to a 0 to 1 score.

- What it measures: the presence of specific known AI tells, nothing else.
- Why I chose it: it is a completely different lens from the other two. It does not read
  meaning and it does not measure distribution shape, it just matches a lexicon.
- What it misses: it is high precision but low recall. When a marker is present that is
  strong evidence, but plenty of AI text avoids these phrases, and some humans use them.
  Because absence is only weak evidence, no markers scores 0 (leaning human), and this
  signal carries the smallest weight.

### Combining them (the ensemble vote)

```
ai_likelihood = 0.55 * llm_score + 0.25 * stylometry_score + 0.20 * lexical_score
```

The LLM leads because a holistic read is the strongest evidence. Stylometry is next.
Lexical markers get the smallest weight because they are high precision but sparse. The
weights are renormalized over whichever signals are present, which is what makes the
multi-modal case clean: for a short image caption, stylometry is skipped and the vote runs
over the LLM and lexical signals only.

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

| input | llm | stylometry | lexical | ai_likelihood | bucket |
|---|---|---|---|---|---|
| clearly AI essay | 0.80 | 0.627 | 0.80 | 0.757 | likely_ai |
| clearly human (casual review) | 0.20 | 0.196 | 0.00 | 0.159 | likely_human |
| borderline: formal human (economics) | 0.60 | 0.744 | 0.00 | 0.516 | uncertain |
| borderline: lightly edited AI | 0.60 | 0.403 | 0.40 | 0.511 | uncertain |

This is what "meaningful" means to me: the clear cases sit about 0.60 apart, all three
buckets are reachable, and both borderline cases land in the middle instead of getting a
confident verdict. The formal economics text is the important one. The stylometry signal
actually leans AI on it (0.744, because formal academic writing really is uniform), but the
LLM and lexical signals pull it back, so it lands in `uncertain` rather than `likely_ai`.
That is exactly the false positive the system is built to avoid.

### Two example submissions

High confidence result:

```
text:       "ok so i finally tried that new ramen place downtown and honestly?
             underwhelming. the broth was fine but they put WAY too much sodium..."
llm_score:        0.2
stylometry_score: 0.196
lexical_score:    0.0
ai_likelihood:    0.159
confidence:       0.841
attribution:      likely_human
```

Lower confidence result:

```
text:       "I've been thinking a lot about remote work lately. There are genuine
             tradeoffs, flexibility and no commute on one side, isolation and blurred
             work-life boundaries on the other. Studies show productivity varies..."
llm_score:        0.6
stylometry_score: 0.403
lexical_score:    0.4
ai_likelihood:    0.511
confidence:       0.511
attribution:      uncertain
```

The first is a confident human call at 0.841. The second is a lightly edited AI passage
that sits almost on the fence at 0.511, so the system says it cannot tell rather than
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
renders as "rated it about 84% likely to be written by a person," and the clearly AI essay
renders as "about 76% likely to be machine written." When the creator holds a verified
human credential (see stretch features), one more sentence is appended: "The creator of
this piece holds a verified human credential."

## Appeals workflow

A creator who thinks the classification is wrong sends `POST /appeal` with the
`content_id` from their submission and a `creator_reasoning` string. The system:

1. Looks up the original decision by content_id (returns 404 if it does not exist).
2. Sets that content's status to `under_review`.
3. Writes an appeal row to the audit log carrying the reasoning and pointing back at the
   original decision (its attribution, confidence, and all three signal scores).
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
      "content_id": "7ad761f2-7d88-4ef3-add9-8ea164f4cb7c",
      "creator_id": "econ_lena",
      "content_type": "text",
      "timestamp": "2026-07-01T06:09:48.320Z",
      "attribution": "uncertain",
      "ai_likelihood": 0.516,
      "confidence": 0.516,
      "llm_score": 0.6,
      "stylometry_score": 0.744,
      "lexical_score": 0.0,
      "status": "under_review",
      "appeal_reasoning": "This is my own analysis for a graduate economics seminar. English is my second language and my academic style reads as formal."
    },
    {
      "id": 3,
      "event": "classification",
      "content_id": "7ad761f2-7d88-4ef3-add9-8ea164f4cb7c",
      "creator_id": "econ_lena",
      "content_type": "text",
      "timestamp": "2026-07-01T06:09:47.704Z",
      "attribution": "uncertain",
      "ai_likelihood": 0.516,
      "confidence": 0.516,
      "llm_score": 0.6,
      "stylometry_score": 0.744,
      "lexical_score": 0.0,
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 2,
      "event": "classification",
      "content_id": "f12c7ecc-4354-4c3a-96c7-1b3d9c42bda6",
      "creator_id": "writer_jon",
      "content_type": "text",
      "timestamp": "2026-07-01T06:09:46.112Z",
      "attribution": "likely_human",
      "ai_likelihood": 0.159,
      "confidence": 0.841,
      "llm_score": 0.2,
      "stylometry_score": 0.196,
      "lexical_score": 0.0,
      "status": "classified",
      "appeal_reasoning": null
    },
    {
      "id": 1,
      "event": "classification",
      "content_id": "8c1306a9-139d-40fa-8693-8143bfda1dd8",
      "creator_id": "poet_maya",
      "content_type": "text",
      "timestamp": "2026-07-01T06:09:45.542Z",
      "attribution": "likely_ai",
      "ai_likelihood": 0.757,
      "confidence": 0.757,
      "llm_score": 0.8,
      "stylometry_score": 0.627,
      "lexical_score": 0.8,
      "status": "classified",
      "appeal_reasoning": null
    }
  ]
}
```

Each row carries the timestamp, content id, creator, content type, attribution, confidence,
all three individual signal scores, the combined likelihood, the status, and the appeal
reasoning when there is one.

## Stretch features

All four are built and working.

### 1. Ensemble detection (three signals)

The pipeline uses three signals instead of two: the Groq LLM, stylometry, and the lexical
AI marker detector described above. They are combined with a documented weighted vote,
`0.55*llm + 0.25*stylometry + 0.20*lexical`, renormalized over the signals that apply. The
lexical signal earns its keep in the calibration table: it pushes the clearly AI essay up
to 0.757 (it hits "it is important to note" and "furthermore") and nudges the lightly
edited AI passage up with "studies show," while staying silent on both human texts so it
never accuses them.

### 2. Provenance certificate (verified human credential)

A creator can earn a verified human credential and it shows on their content.

- `POST /verify/start` with a `creator_id` returns a random writing prompt and a
  `challenge_id`.
- `POST /verify/complete` with the `creator_id`, `challenge_id`, and their written response
  runs that response through the detection pipeline. If it reads as clearly human, the
  system grants a certificate (a `certificate_id` stored against the creator). If it reads
  AI, it is denied with the score and they can try again. A challenge can only be used once.
- Once verified, every `/submit` response for that creator carries `creator_verified: true`
  and the transparency label gains the line "The creator of this piece holds a verified
  human credential." `GET /creator/<creator_id>` returns the credential status.
- Honest limit: this proves the creator can produce human reading writing on demand, not
  that any specific later piece is theirs. It is a trust signal, not proof.

Example: a creator wrote a casual few sentences about a burned breakfast for their
challenge (ai_likelihood 0.235, likely_human) and earned the credential. A response full of
AI cliches ("a testament to the rich tapestry of nature. Furthermore...") scored 0.829 and
was correctly denied.

### 3. Analytics dashboard

`GET /analytics` returns JSON, and `GET /dashboard` renders it as a plain server side HTML
page (simple bars, no JavaScript). It shows the attribution breakdown across the three
buckets, the appeal rate (appeals over classifications), average confidence, the average
score per signal, a breakdown by content type, and the number of verified human creators
(the extra metric). Example `/analytics` output:

```json
{
  "total_submissions": 3,
  "by_attribution": { "likely_ai": 0, "uncertain": 1, "likely_human": 2 },
  "by_content_type": { "image_caption": 2, "text": 1 },
  "appeals": 0,
  "appeal_rate": 0.0,
  "avg_confidence": 0.76,
  "avg_ai_likelihood": 0.298,
  "avg_signal_scores": { "llm": 0.367, "stylometry": 0.5, "lexical": 0.0 },
  "verified_human_creators": 1
}
```

### 4. Multi-modal support

`POST /submit` takes an optional `content_type`, either `text` (the default) or
`image_caption` for an alt text or caption. Captions are short, so the stylometry signal is
skipped (it needs length to be reliable) and the weighted vote renormalizes over the LLM
and lexical signals. The LLM also switches to a caption specific prompt, since the tells are
different: AI captions tend to be generic and describe the obvious, human captions tend to
be specific or offhand. Everything downstream (label, appeal, log, analytics) treats both
types the same, with `content_type` recorded on every row.

Example: the generic caption "A serene landscape featuring a majestic mountain range under
a clear blue sky" scored 0.587 (uncertain), while "my kid's science fair volcano, took
third, he is furious lol" scored 0.073 (likely_human, 93% confidence).

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
  economics example in the calibration table is real: the stylometry signal leans AI on it
  because careful, even, textbook style prose reads as uniform. The 0.70 threshold and the
  LLM pulling the other way catch many of these in the uncertain band instead of accusing
  them, and the appeal path exists for the rest, but the system genuinely cannot distinguish
  polished human prose from AI with confidence. That is an honest limit, not a bug I can
  tune away.

- **No auth anywhere.** Anyone with a content_id can appeal, anyone can read the log or the
  dashboard, and the verified human credential proves willingness and ability to write
  human text on demand, not authorship of any later piece. Fine for this project, not for
  production.

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
app.py               Flask app: submit, appeal, log, verify, creator, analytics, dashboard
signals.py           Signal 1 (Groq LLM), Signal 2 (stylometry), Signal 3 (lexical markers)
scoring.py           combine_signals (weighted vote), attribution buckets, confidence
labels.py            the three transparency label variants, plus the credential line
db.py                SQLite storage: submissions, audit_log, creators, challenges, analytics
test_signal.py       manual check of Signal 1 alone
test_calibration.py  four input calibration of the full three signal pipeline
planning.md          the spec, written before the code, with the stretch designs
requirements.txt
```
