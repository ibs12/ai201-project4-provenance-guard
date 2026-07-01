# Provenance Guard

A backend a creative platform can plug in to check whether a piece of writing looks human
or AI generated. It doesnt try to be a perfect detector, because perfect AI detection isnt
a solved problem. It runs three independent signals, reports how sure it is in plain
language, and lets creators appeal when it gets them wrong.

One idea drives the whole thing: on a platform for creative work, calling a real persons
writing "AI generated" is the worst mistake it can make. So it needs stronger evidence to
accuse than to clear, and every decision comes with an appeal path.

All four stretch features are built too (third signal, verified human credential, analytics
dashboard, image caption support). They have their own section below.

## How it works

The path a submission takes:

```
POST /submit {text, creator_id, content_type?}
   -> Signal 1 Groq LLM        -> llm_score        (semantic read)
   -> Signal 2 stylometry      -> stylometry_score (writing stats, text only)
   -> Signal 3 lexical markers -> lexical_score    (known AI phrases)
   -> vote: ai_likelihood = 0.55*llm + 0.25*stylometry + 0.20*lexical
   -> attribution bucket + confidence
   -> transparency label (+ a credential line if the creator is verified)
   -> write a row to the SQLite audit log
   -> response
```

Appeals are shorter: send the content_id and your reasoning, the system flips the status to
`under_review`, logs the appeal next to the original decision, and confirms. It doesnt
rerun detection. The full diagram for both flows is in [planning.md](planning.md).

## API

Required:

| Method | Path | Body | Returns |
|--------|------|------|---------|
| POST | /submit | `text, creator_id, content_type?` | content_id, attribution, confidence, ai_likelihood, llm_score, stylometry_score, lexical_score, label, creator_verified, status |
| POST | /appeal | `content_id, creator_reasoning` | content_id, status, appeal_id, message |
| GET | /log | none | `{ entries: [...] }` |

Stretch:

| Method | Path | Returns |
|--------|------|---------|
| POST | /verify/start | challenge_id, prompt |
| POST | /verify/complete | verified, certificate_id, message |
| GET | /creator/&lt;id&gt; | verified_human, certificate_id |
| GET | /analytics | detection patterns, appeal rate, verified count |
| GET | /dashboard | HTML view |

`content_type` defaults to `text`, the other option is `image_caption`. The GET endpoints
have no auth here so the log and stats are easy to read for grading, in production theyd be
locked down.

## Setup

```
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Put your Groq key in a `.env` file (its gitignored, dont commit it):

```
GROQ_API_KEY=your_key_here
```

Then `python app.py` and:

```
curl -s -X POST http://localhost:5000/submit \
  -H "Content-Type: application/json" \
  -d '{"text": "The sun dipped below the horizon, painting the sky amber and rose.", "creator_id": "test-user-1"}' | python -m json.tool
```

## Detection signals

Three signals looking at different things. One reads meaning, one measures shape, one
matches a fixed list of AI tells. Thats what makes combining them worth more than any one
alone.

**Signal 1, Groq LLM (semantic).** `llama-3.3-70b-versatile` reads the passage like a
person would and returns a probability its AI plus a short reason for the log. It catches
fluent filler that says very little. What it misses: its a black box, it can be fooled by
lightly edited AI, and it can flag careful or formal writing as too clean. I run it at
temperature 0 to keep it stable.

**Signal 2, stylometry (pure Python).** Three stats, each ramped to a 0 to 1 AI-ness score
and averaged: sentence length variation, type token ratio (moving average over 40 word
windows so length doesnt skew it), and punctuation variety. AI text is more uniform on all
three. What it misses: it has no idea what the text says, its unreliable under about 30
words (so it goes neutral there), and it reads deliberately uniform human writing like
formal prose or repetitive poetry as AI.

**Signal 3, lexical markers (the ensemble stretch).** A fixed vocabulary of phrases that
show up way more in AI writing, like "it is important to note", "a testament to", "delve
into", "furthermore", "studies show". Counts distinct hits and maps to a score. Its high
precision but low recall, so no markers scores 0 (leaning human) and it carries the smallest
weight.

**Combining:** `ai_likelihood = 0.55*llm + 0.25*stylometry + 0.20*lexical`, renormalized
over whichever signals are present. The LLM leads because a holistic read is the strongest
evidence. Lexical is smallest because its sparse. For a short caption stylometry drops out
and the vote runs over the other two.

## Confidence scoring

Two numbers, on purpose:

- `ai_likelihood` (0 to 1): how likely its AI. The honest core number.
- `confidence` = `max(ai_likelihood, 1 - ai_likelihood)`: how hard it leans. Near 0.5 is a
  coin flip, near either end is sure.

The bucket comes from `ai_likelihood` with lopsided thresholds:

```
ai_likelihood >= 0.70  -> likely_ai      (high bar to accuse)
ai_likelihood <= 0.40  -> likely_human    (lower bar, benefit of the doubt)
in between             -> uncertain
```

That gap between 0.70 and 0.40 is the false positive rule. The AI zone is 0.30 wide, the
human zone is 0.40 wide, so a borderline case tilts toward not accusing the writer.

**How I checked the scores mean something.** I ran four inputs I picked on purpose (in
[test_calibration.py](test_calibration.py)), one clearly AI, one clearly human, two
borderline. It prints each signal separately so I can see whats driving the result:

| input | llm | stylometry | lexical | ai_likelihood | bucket |
|---|---|---|---|---|---|
| clearly AI essay | 0.80 | 0.627 | 0.80 | 0.757 | likely_ai |
| clearly human review | 0.20 | 0.196 | 0.00 | 0.159 | likely_human |
| borderline, formal human (economics) | 0.60 | 0.744 | 0.00 | 0.516 | uncertain |
| borderline, lightly edited AI | 0.60 | 0.403 | 0.40 | 0.511 | uncertain |

The clear pair sits about 0.60 apart, all three buckets are reachable, and both borderline
cases land in the middle. The economics one matters most: stylometry leans AI on it (0.744,
because formal academic prose really is uniform), but the LLM and lexical signals pull it
back so it lands uncertain instead of likely_ai. Thats the false positive the system exists
to avoid.

**Two examples with different confidence.** High confidence, the ramen review:

```
llm 0.2, stylometry 0.196, lexical 0.0 -> ai_likelihood 0.159, confidence 0.841 -> likely_human
```

Lower confidence, a lightly edited AI passage about remote work:

```
llm 0.6, stylometry 0.403, lexical 0.4 -> ai_likelihood 0.511, confidence 0.511 -> uncertain
```

Same pipeline, very different confidence. The second one sits almost on the fence, so the
system says it cant tell rather than guessing.

## Transparency label

One variant per bucket, written for a normal reader, never claiming certainty. The AI and
human ones show confidence as a percentage, the uncertain one leaves the number out on
purpose (a confident figure on an inconclusive result sends the wrong message).

**High confidence AI** (`likely_ai`):

> This content shows strong signs of being AI generated. Our system rated it about
> {confidence}% likely to be machine written, based on its writing style and language
> patterns. This is an automated estimate, not a final judgment. If you wrote this
> yourself, you can appeal and a person will take another look.

**High confidence human** (`likely_human`):

> This content looks human written. Our system found little sign of AI generation and
> rated it about {confidence}% likely to be written by a person. This is an automated
> estimate and not a guarantee of authorship.

**Uncertain** (`uncertain`):

> We could not tell whether a person or an AI wrote this. The signals were mixed, so we
> are not labeling it either way. Please treat this as inconclusive rather than a verdict.

At runtime `{confidence}` gets filled in, so the ramen review reads "about 84% likely to be
written by a person" and the AI essay reads "about 76% likely to be machine written." If the
creator is verified, one line is appended: "The creator of this piece holds a verified human
credential."

## Appeals

Send `content_id` and `creator_reasoning`. The system looks up the decision (404 if it
doesnt exist), sets status to `under_review`, logs an appeal row with the reasoning and the
original scores, and returns an appeal_id. No auto reclassification, it just flags the item
for a human, who would see the decision and the reasoning side by side (the log rows where
status is under_review). No login here, so anyone with the content_id can appeal, thats a
known limitation.

```
curl -s -X POST http://localhost:5000/appeal \
  -H "Content-Type: application/json" \
  -d '{"content_id": "PASTE-ID", "creator_reasoning": "I wrote this myself..."}' | python -m json.tool
```

## Rate limiting

Flask-Limiter on /submit, keyed by IP, in memory.

- **Limits: 10 per minute and 100 per day.**
- Why: a real writer submits a handful of pieces in a sitting, not ten a minute, so 10/min
  is well above normal use but stops a script hammering the endpoint. 100/day caps sustained
  abuse from one address while staying far above any real single creator. Each submit also
  calls Groq, so capping bursts protects the free tier.

Evidence, 12 rapid requests:

```
status codes: [200, 200, 200, 200, 200, 200, 200, 200, 200, 200, 429, 429]
200 count: 10   429 count: 2
```

## Audit log

Every classification and appeal is one structured SQLite row (see [db.py](db.py)), not a
print. `GET /log` returns recent rows newest first. Real sample below, three submissions
across all three buckets plus an appeal econ_lena filed after her formal writing landed in
uncertain. The appeal (id 4) sits right on top of the classification it points at (id 3):

```json
{
  "entries": [
    {
      "id": 4, "event": "appeal",
      "content_id": "7ad761f2-7d88-4ef3-add9-8ea164f4cb7c",
      "creator_id": "econ_lena", "content_type": "text",
      "timestamp": "2026-07-01T06:09:48.320Z",
      "attribution": "uncertain", "ai_likelihood": 0.516, "confidence": 0.516,
      "llm_score": 0.6, "stylometry_score": 0.744, "lexical_score": 0.0,
      "status": "under_review",
      "appeal_reasoning": "This is my own analysis for a graduate economics seminar. English is my second language and my academic style reads as formal."
    },
    {
      "id": 3, "event": "classification",
      "content_id": "7ad761f2-7d88-4ef3-add9-8ea164f4cb7c",
      "creator_id": "econ_lena", "content_type": "text",
      "timestamp": "2026-07-01T06:09:47.704Z",
      "attribution": "uncertain", "ai_likelihood": 0.516, "confidence": 0.516,
      "llm_score": 0.6, "stylometry_score": 0.744, "lexical_score": 0.0,
      "status": "classified", "appeal_reasoning": null
    },
    {
      "id": 2, "event": "classification",
      "content_id": "f12c7ecc-4354-4c3a-96c7-1b3d9c42bda6",
      "creator_id": "writer_jon", "content_type": "text",
      "timestamp": "2026-07-01T06:09:46.112Z",
      "attribution": "likely_human", "ai_likelihood": 0.159, "confidence": 0.841,
      "llm_score": 0.2, "stylometry_score": 0.196, "lexical_score": 0.0,
      "status": "classified", "appeal_reasoning": null
    },
    {
      "id": 1, "event": "classification",
      "content_id": "8c1306a9-139d-40fa-8693-8143bfda1dd8",
      "creator_id": "poet_maya", "content_type": "text",
      "timestamp": "2026-07-01T06:09:45.542Z",
      "attribution": "likely_ai", "ai_likelihood": 0.757, "confidence": 0.757,
      "llm_score": 0.8, "stylometry_score": 0.627, "lexical_score": 0.8,
      "status": "classified", "appeal_reasoning": null
    }
  ]
}
```

Each row carries the timestamp, content id, creator, content type, attribution, confidence,
all three signal scores, the combined likelihood, status, and the appeal reasoning when
there is one.

## Stretch features

All four are built and working.

**1. Ensemble (three signals).** The pipeline uses the lexical marker signal on top of the
LLM and stylometry, combined with the documented `0.55/0.25/0.20` vote. It earns its keep in
the calibration table, it pushes the AI essay up to 0.757 (hits on "it is important to note"
and "furthermore") and stays silent on both human texts so it never accuses them.

**2. Provenance certificate.** A creator can earn a verified human credential.
`POST /verify/start` gives back a random writing prompt and a challenge_id.
`POST /verify/complete` runs their written response through detection and, if it reads
clearly human, grants a certificate. After that every `/submit` for them carries
`creator_verified: true` and the label gains a credential line. `GET /creator/<id>` shows
status. Its a trust signal, not proof, it shows they can write human text on demand, not
that a later piece is theirs. Tested: a casual few sentences about a burned breakfast earned
it (0.235), a response full of cliches was denied (0.829).

**3. Analytics dashboard.** `GET /analytics` returns JSON and `GET /dashboard` renders it as
a plain HTML page (bars, no JavaScript): attribution breakdown, appeal rate, average
confidence, average per signal, breakdown by content type, and verified creator count (the
extra metric). Sample:

```json
{
  "total_submissions": 3,
  "by_attribution": { "likely_ai": 0, "uncertain": 1, "likely_human": 2 },
  "by_content_type": { "image_caption": 2, "text": 1 },
  "appeals": 0, "appeal_rate": 0.0,
  "avg_confidence": 0.76, "avg_ai_likelihood": 0.298,
  "avg_signal_scores": { "llm": 0.367, "stylometry": 0.5, "lexical": 0.0 },
  "verified_human_creators": 1
}
```

**4. Multi-modal.** `/submit` takes a `content_type`, `text` or `image_caption`. Captions are
short so stylometry is skipped and the vote runs over the LLM and lexical signals, and the
LLM uses a caption specific prompt (AI captions are generic and state the obvious, human ones
are specific or offhand). Everything downstream treats both the same. Tested: "A serene
landscape featuring a majestic mountain range under a clear blue sky" scored 0.587
(uncertain), "my kid's science fair volcano, took third, he is furious lol" scored 0.073
(likely_human).

## Known limitations

- **Repetitive or simple poetry.** Refrains, short even lines, plain vocabulary all look
  statistically uniform, which is exactly what stylometry reads as AI. Low type token ratio
  and low sentence variation both push its score up on real human art. The clearest false
  positive risk, and a direct result of stylometry measuring shape without meaning, its a big
  reason that signal is weighted below the LLM.
- **Formal or academic human writing, incl. non native English.** The economics row in the
  table is real, stylometry leans AI because the prose is careful and even. The 0.70 bar and
  the LLM catch a lot of these in uncertain instead of accusing them, but the system honestly
  cant tell polished human prose from AI with confidence. Thats a real limit, not a bug I can
  tune away.
- **No auth anywhere.** Anyone with a content_id can appeal, anyone can read the log or
  dashboard, and the credential only proves someone can write human text on demand. Fine here,
  not for production.

## Spec reflection

**Where the spec helped.** Writing the three label variants and the confidence design in
planning.md before any code kept me from building a binary detector. Deciding up front that
it would report two separate numbers (ai_likelihood and confidence) with lopsided thresholds
meant the scoring and label code was basically a translation of the spec, the label text was
already agreed on by the time I wrote the function.

**Where it diverged.** planning.md set the AI threshold at 0.75 and guessed the type token
anchors at 0.72 and 0.45. Calibration proved both wrong, the formal economics text tipped
into likely_ai and the clearly AI essay undershot into uncertain. Turned out the type token
anchors were way below the metrics real range so every normal text pinned that metric at 0
and it never mattered, and the LLM was treating formality itself as an AI tell. I fixed the
anchors to the real range (0.65 to 0.95), rewrote the LLM prompt to weigh substance over
polish, and dropped the threshold to 0.70. Then all four inputs landed right, and I updated
planning to match.

## AI usage

I used an AI coding tool throughout, driven by the planning sections. Two spots where I had
to override it:

1. **The LLM prompt.** Its first version asked the model how "polished" and "evenly written"
   the text was. That scored the formal economics passage 0.8, same as an obvious AI essay,
   which wouldve been a false positive on real human writing. I rewrote it around a different
   idea: fluent filler that says little is the tell, specific checkable claims lean human even
   when formal. That dropped the economics text to 0.6 without moving the real AI case.
2. **The stylometry anchors.** The mapping it generated looked fine but the type token anchors
   were set so any normal length text scored 0 on that metric, so it silently did nothing and
   dragged AI scores down. I caught it by printing the raw metric values during calibration,
   fixed the anchors, and retested. Exactly the "looks fine but quietly wrong" thing the brief
   warned about.

## Files

```
app.py               Flask app: submit, appeal, log, verify, creator, analytics, dashboard
signals.py           the three signals
scoring.py           the weighted vote, buckets, confidence
labels.py            the three label variants + the credential line
db.py                SQLite: submissions, audit_log, creators, challenges, analytics
test_signal.py       quick check of signal 1 alone
test_calibration.py  the four input calibration
planning.md          the spec, written before the code
```
