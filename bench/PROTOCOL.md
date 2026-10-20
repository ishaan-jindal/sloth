# Sloth benchmark protocol (pre-registered)

Written **before any model was run**. Thresholds, rubric, corpus and model list are
frozen at this commit. Any change after seeing results is labelled *exploratory* and
excluded from the headline numbers.

## Question

For a developer laptop with **no discrete GPU**, what is the smallest open-weight
model that produces commit messages that a human would actually commit unedited?

## Claim under test

> A local model at or under 3B parameters, driven by a structured-output prompt over a
> bounded staged diff, writes commit messages that are indistinguishable from a
> competent human's — with no per-commit inference fee and no code leaving the machine.

This is falsifiable. If the smallest qualifying model turns out to be 7B+, or if no
model beats a trivial template, the thesis is wrong.

## Hardware (fixed, stated with the results)

| | |
|---|---|
| CPU | Intel Core i7-1165G7 @ 2.80GHz, 4C/8T, AVX2 + AVX-512, no AMX |
| GPU | none discrete; Intel Iris Xe (i915, Vulkan 1.4 / Mesa 26.2.5) |
| RAM | 15 GiB total, ~10 GiB available |
| OS | Arch Linux, kernel 7.2.11, bare metal |
| Runtime | Ollama 0.40.6 (local), `num_ctx` set explicitly per run |
| Quantization | Q4_K_M for every model |

## Corpus

Real commits, real diffs, from the author's public repos. Sources and cutoffs are in
`capture.py`; fixtures land in `cases.jsonl`.

Inclusion rules (applied mechanically, before review):
- no merge commits
- not a bot/dependabot commit
- diff present and non-empty
- subject non-empty and > 10 characters

Stratification (fixed quotas, not "whatever we had"):

| Repo | Convention | Cases | Purpose |
|---|---|---|---|
| matcha | 804/836 conventional, scoped | 30 | primary; `(#NNNN)` PR suffix in subjects |
| runnix | 28/30 conventional, unscoped | 15 | clean post-cutoff set, 2026-09-03+ |
| vaults-hub | 57/67 conventional, scoped | 15 | release-as-commit (`chore(release): bump to 0.3.0`) |
| termchat | 133/206 conventional, mixed | 10 | mixed style, many merges, component tags `cli-v2.0.0` |
| portfolio-website | 38/99 conventional | 10 | freeform fallback path |

**Contamination control.** All five repos are public on GitHub and may be in a model's
training data. Every case is tagged `post_cutoff_safe` (committed after 2026-09-01,
i.e. after every candidate model's likely cutoff). The headline number uses
**`post_cutoff_safe == true` cases only**; the rest are reported separately as
`historical`.

**Human messages are a reference, not ground truth.** The author's own commit messages
are often careless. They are used for (a) mining the repo's convention, and (b) one
comparison arm in the blind test — never as a correctness oracle.

## Rubric (three independent axes — do not conflate)

| Axis | Method | Score |
|---|---|---|
| **A. Conformance** | deterministic: does the assembled message match the repo's mined convention (prefix regex, scope vocabulary, subject length, no trailing period)? | pass rate % |
| **B. Factual fidelity** | human: does the message claim anything the diff does not support (a file, function, or behaviour that is not there)? | % outputs with zero unsupported claims; unsupported-claim count per output |
| **C. Usefulness** | human, blind: shuffled head-to-head, model vs human message — "which would you commit unedited?" plus a 1–5 rating | win rate %, mean rating |

Axis A is a *format* check and says nothing about B or C. Axis B is the one that
catches the failure mode a validator cannot see: a perfectly well-formed message
about a change that did not happen.

## Arms

1. **No-model baseline** — deterministic template from the diffstat
   (`<type>(<scope>): update <n> files` / `update <file>`). Free, instant, and the
   floor every model must clear.
2. **Human reference** — the original commit message.
3. **Model arms** — one prompt, one schema, one set of sampling settings; only the
   model changes.

### Model ladder (all Q4_K_M, all local)

| Model | Params | Note |
|---|---|---|
| `qwen3:0.6b` | 0.6B | floor probe |
| `qwen2.5-coder:1.5b` | 1.5B | |
| `qwen2.5-coder:3b` | 3B | |
| `llama3.2:3b` | 3B | |
| `gemma3:4b` | 4B | ceiling probe |

Plus `gemma4:e2b` (4.6B total / 2B effective) and `spike-qwen15b` (1.8B) if present
locally. `spike-qwen15b` has non-standard provenance, so it is reported separately and
never used for the headline claim.

**Confound control:** quantization, prompt, schema, `num_ctx`, temperature and
sampling are identical across arms. Only the model varies.

## Fixed settings

```
temperature 0.2, top_p 0.9, num_ctx 8192 (explicit), keep_alive -1,
seed fixed, response_format = JSON schema, single call per case
```

`num_ctx` is set explicitly because Ollama's default is small enough to silently
truncate the prompt; any run where the prompt exceeded the context is **discarded and
reported**, never scored.

## Pre-registered thresholds — a model "qualifies" only if it clears all four

| # | Threshold | Value |
|---|---|---|
| T1 | Conformance (axis A) | ≥ 95% of cases |
| T2 | Factual fidelity (axis B) | ≥ 90% of outputs with zero unsupported claims |
| T3 | Blind preference (axis C) | win rate ≥ 50% vs the human message, i.e. ties-or-better |
| T4 | Latency, warm | median ≤ 8 s, p95 ≤ 20 s per commit on the hardware above |

Plus, as a gate on the whole thesis:

> **T5** the qualifying model must beat the no-model baseline on axis C by a margin
> that is not noise (≥ 65% win rate vs baseline), otherwise the model is decoration
> and the honest claim is "the template was enough".

## Decision gate

- If a model ≤ 3B clears T1–T5 → **thesis confirmed**. Build the CLI.
- If the smallest qualifying model is 4B → thesis survives but "sloth-paced laptop"
  narrows to "4B on a 15 GiB laptop"; report it plainly.
- If no model clears T5 → **stop**. Either the thesis is wrong, or the tool's value
  is elsewhere (deterministic convention detection, not model wording). Re-scope
  before writing Rust.
- If a model clears T1 but fails T2 → the model is a fluent liar. The post's claim
  changes to "format-compliant, factually unreliable" and the tool ships with a
  mandatory human read of the diff.

## Known limits of this protocol (state these in the post)

- Repos are the author's own; the prompt will have been tuned on them. Mitigated
  only by `post_cutoff_safe` filtering, not eliminated.
- "Would you commit this unedited?" is one person's judgement, n=1 rater.
- Conformance is measured against conventions mined from the same history the
  benchmark uses.
- Some repos embed `(#1234)` PR suffixes in subjects. A local tool cannot know the
  PR number, so conformance has a ceiling on those repos. Measure it, report it.
- 4 cores, no discrete GPU. Results do not transfer to a machine with a GPU.

---

## Realised corpus (frozen at capture time, before any model was run)

`capture.py` → `cases.jsonl` (80 cases), `skips.jsonl` (20 audited skips).

| Repo | Captured | `post_cutoff_safe` | mean diff |
|---|---|---|---|
| matcha | 30 | 15 | 9.2 KB |
| runnix | 15 | 15 | 12.4 KB |
| vaults-hub | 15 | 15 | 5.5 KB |
| termchat | 10 | 10 | 2.9 KB |
| portfolio-website | 10 | 10 | 6.5 KB |
| **total** | **80** | **65** | — |

Safe-case window: **2026-09-23 → 2026-10-19**. Diff sizes across safe cases:
median 3.9 KB, p90 20.6 KB, max 54.7 KB — the p90 fits an 8192-token context with a
short prompt; anything over budget must be discarded-and-reported, never scored.

Skips, all mechanical: 17 bot/dependabot, 2 diff > 60 KB, 1 short subject. No quota
went unmet, no case was dropped by hand.

**Headline corpus = the 65 `post_cutoff_safe` cases.** The other 15 are reported
separately as `historical` and never used for the primary claim.

---

## Amendments (logged before the benchmark run, not after)

Each amendment is a **defect fix or a setting made explicit**, never a change to a
threshold or to the rubric. Thresholds T1–T5 and the rubric above are untouched.

### A1 — `think: false` is now a pinned request field
`gemma4:e2b` has thinking enabled by default. Under a JSON-schema `format`
constraint its thinking tokens are generated but never reach `message.content`; the
call returns `done_reason: "length"`, `eval_count: 400`, and an **empty** content
string — indistinguishable from a broken model. With `"think": false` the same
request returns clean JSON in ~9 s. Every arm sends it; Ollama ignores it for
models that do not support thinking. Recorded in `results.jsonl` for reproducibility.

### A2 — the diff is sent once, not twice
The prompt template carried the diff, the file list and the diffstat in both the
system half and the user half. That doubles the prompt and roughly doubles prefill
time, which dominates latency on a CPU-only machine. The renderer strips the
`## Files changed` / `## Diffstat` / `## Diff` blocks from the system half. The
`{{VARIABLE}}` contract and the `--- USER ---` marker are unchanged.

### A3 — pilot observations (2 cases, 2 locally present models, not scored)
Recorded for transparency. These are **not** benchmark results and no threshold was
changed in response to them.

- **Latency is 25–46 s per commit on this hardware**, of which the large majority is
  prefill (~1900 prompt tokens) rather than decode (37–57 tokens). T4's 8 s median
  target is therefore at serious risk. T4 is left as written; if it fails, that is
  the finding, and the honest claim becomes "privacy and zero fee, at a real time
  cost" rather than "fast".
- **Both pilot models picked a different scope than the human.** The human wrote
  `feat(export):`; both models wrote `feat(tui):`. Note that `tui` is a *real* scope
  in matcha's vocabulary (8 occurrences), so this is not an out-of-vocabulary
  invention — it is a plausible scope that may or may not match the change. Whether
  it is wrong is exactly what the fidelity axis has to adjudicate, and it is the
  reason the rubric scores accuracy and conformance separately.
- **`gemma4:e2b` invented a pull-request number** — `(#1234)` — in a repo whose real
  subjects carry `(#1745)`, despite an explicit instruction not to. The exemplars it
  was shown had their numbers stripped, so this is invention, not copying. **A repo
  whose convention embeds a PR number has a conformance ceiling that no prompt can
  fix**, because the number is not knowable from a local diff. The tool must strip
  fabricated `(#N)` tokens in code. Recorded as a design requirement for the CLI.

### A4 — commit bodies counted trailers as prose
The style miner decided "does this repo write commit bodies?" by counting any
non-empty body. On these repos that was badly wrong: `Signed-off-by:` and
`Co-authored-by:` trailers are not commit bodies, and runnix's 28 bodies are 24
trailers and 4 real prose. Unfixed, the prompt would have told the model runnix uses
bodies 100% of the time. Trailer-only bodies are now excluded.

Measured before and after, across all five repos:

| Repo | body_usage (fixed) | trailer-only rate | dominant headings |
|---|---|---|---|
| matcha | 0.81 | 0.03 | `## What?`, `## Why?` (81%) |
| vaults-hub | 0.47 | 0.49 | — |
| termchat | 0.25 | 0.34 | — |
| portfolio-website | 0.20 | 0.30 | — |
| runnix | 0.14 | 0.86 | — |

The "writes prose bodies" line is `body_usage >= 0.35`. The choice is biased
deliberately: telling a model to write a body when the repo rarely does adds
visible noise to most commits, while missing a body on the occasional commit costs
less. The heading structure is now mined too, so the prompt can name `## What?` /
`## Why?` for matcha instead of asking for "2-4 lines of plain prose".

This changes a **prompt-construction** rule, not an evaluation threshold. T1–T5 and
the rubric are unchanged.

### A5 — the subject pattern is a generation constraint, not a validation rule
The schema sent to the model was static, with no constraint on the subject's shape.
Measured consequence: with a plain schema, **every** model tested dropped the
`type(scope):` prefix that matcha's convention requires — `qwen3:0.6b` produced the
bare word `add`, `gemma4:e2b` produced `integrate palette opening into email update
flow`. The prefix was described in the prompt but nothing required it, and small
models do not infer a requirement from a description.

Ollama compiles the JSON schema into a grammar and masks every token that cannot
continue a valid string. A `pattern` on the subject field therefore makes a
non-conforming subject **unreachable** rather than merely invalid. With a patterned
schema, all three locally present models produced a conforming prefix, and
generation was *faster* — 0.4 s against 2.2 s on the same prompt — because the
grammar prunes the search space.

The schema is now built per repository from the mined convention. Pattern forms
verified against the compiler:

| shape | pattern |
|---|---|
| conventional | `^(feat\|fix\|chore\|docs\|ci\|test)(\([a-z0-9._/-]+\))?!?: .+$` |
| gitmoji | `^:[a-z0-9_+-]+: .+$` |
| ticket | `^[A-Z][A-Z0-9]+-[0-9]+:? .+$` |
| freeform | `^.{1,72}$` |

Compiler behaviour established by experiment, because two of these fail silently:

- a **top-level** alternation of alternatives is enforced;
- a **nested** alternation inside a group is accepted and then **not enforced** —
  the model produces non-matching output and the schema appears to work;
- a **negated character class** (`[^.]`) corrupts generation outright: the model
  emits malformed JSON and drops fields;
- non-capturing groups and `?` quantifiers are fine.

T1 is unchanged, but its meaning shifts: with the prefix now guaranteed by the
grammar, T1 measures whether the model sticks to the repository's **scope
vocabulary**, and the report breaks out prefix conformance separately so the
schema's contribution is visible rather than hidden inside a combined number.

The scope is deliberately **not** constrained to the mined vocabulary. A new
subsystem can legitimately need a scope the repo has never used, and forcing the
model into an existing one would trade a real error for a fake one. The tool flags
an unseen scope for the user instead.

### A6 — a missing scope is only a violation in a repo that always scopes
`scope_known` failed any subject without a scope whenever the repository had *any*
scoped commits. Most repositories mix scoped and unscoped subjects, so this failed
correct subjects. The check now compares against `scope_rate`, the fraction of the
repository's conventional subjects that carry a scope, and only treats a missing
scope as a violation at or above 90%.

Measured: matcha 0.32, runnix 0.00, vaults-hub 0.72, termchat 0.72. An unscoped
subject is the norm in two of these five repositories.

### A7 — the output cap was truncating long bodies
`num_predict` was 400. The longest prose body among the 65 safe cases is 384
tokens, so subject plus body plus JSON overhead overran the cap: the output was cut
mid-JSON and the case was scored as a parse failure rather than as a bad answer.
That is a systematic bias against exactly the repositories that write long bodies.
The cap is now 768, above the worst case in the corpus, and it costs nothing when
the model stops early — which constrained decoding makes it do.

The style miner now also reports the median body length in words, and the body rule
tells the model that number, so a model does not pad a short-bodied repository out
to a long one. Measured across the five repositories:

| repo | prose body rate | median words | headings |
|---|---|---|---|
| matcha | 0.81 | 47 | `## What?`, `## Why?` |
| runnix | 0.14 | 85 | — |
| vaults-hub | 0.47 | 50 | — |
| termchat | 0.25 | 22 | — |
| portfolio-website | 0.20 | 25 | — |

### A8 — the deterministic fidelity check was unsound
The first version of the check flagged any word in the subject that did not appear
in the diff. That is not a claim of unsupported intent: "add option" and "enable
feature" are ordinary English that a subject is entitled to use, and the check
flagged them, and it flagged the trivial template baseline as well. A metric that
fires on the baseline measures nothing.

The check now fires only on things that can be verified without judgement:

- an issue or pull-request number, which is never knowable from a local diff
  (observed for real: `gemma4:e2b` emitted `(#1234)`);
- a file name in the subject, which the prompt forbids outright;
- a code identifier — snake_case, CamelCase, `a::b`, `a.b` — that does not occur
  anywhere in the diff.

An unseen scope stays a *note*, not a claim: a new subsystem can legitimately need
a scope the repository has never used. The tool flags it for the user; the
benchmark counts it separately.

### A9 — the two implementations of the miner were compared, and one disagreed
The benchmark's miner is written in Python; the tool's is written in Rust. They
were written from the same rules, which is exactly how two implementations drift
apart. `bench/xcheck.py` runs both over the five benchmark repositories and
compares every field: shape, confidence, PR-suffix habit, prose-body rate,
scope rate, subject-length median and p90, body-word median, the type and scope
vocabularies, the dominant heading sequence, and the first exemplar.

The first comparison found three disagreements:

- **body word median for matcha: Rust 45, Python 47.** The Rust excludes markdown
  heading markers from the word count, so `## What?` is one word rather than two.
  The Rust is right, and the Python now does the same.
- **`type_vocab` and `scope_vocab` were only emitted for a conventional shape by
  the Python, but always by the Rust.** The Python now emits them for every
  shape. This changes no behaviour: a freeform repository never uses the
  vocabularies to build a pattern, and the convention paragraph for a freeform
  repository does not mention types.

Only one prompt in the whole corpus changes as a result: matcha's body-length
hint changes from "around 47 words" to "around 45 words". No other repository's
hint moves, and nothing else in the prompt changes. The thirty matcha cases
already run for `qwen3:0.6b` were re-run with the corrected miner and the old
rows discarded, so every row in the final results comes from one implementation.

`bench/xcheck.py` is a runnable check, and it passes. The two miners agree on
every checked field for all five repositories.

### A10 — latency is measured in a dedicated pass, not during the main run
T4 is a latency threshold, and the main run is not a quiet machine. While the
first arm was running, the same box was downloading a 2 GB model, building the
Rust workspace, and running a cross-implementation check. Those are not idle:
the arm recorded a p50 of 14.6 s, and some of that is the box's fault, not the
model's.

T4 is therefore measured in a **dedicated pass on an otherwise idle machine**,
over the first fifteen cases of each model, with nothing else running. The
latencies recorded during the main run are reported too, labelled as
*measured under load*. The threshold itself does not change: p50 at or under 8 s
and p95 at or under 20 s, and a model that misses it misses it.

This was written down before the quiet pass was run.

### A11 — the body length is a schema constraint, not a request
The first `qwen2.5-coder:1.5b` arm failed two of its first four cases with
truncated JSON: the model was cut off mid-string and never closed the object.
Raising `num_predict` did not help — it just made the model run longer. The
output showed why: the model had entered a repetition loop, emitting the same
sentence over and over, and had started inventing issue and pull-request numbers
in the process — `#1234`, `#5678`, `#9012` — despite the prompt saying "do not
invent a pull-request or issue number". It was writing a `## What?` body for a
repository whose bodies average forty-five words, and it never stopped.

`maxLength` in the JSON schema is enforced by the same grammar compiler that
enforces `pattern`. Measured:

| request | result |
|---|---|
| `maxLength: 300` on the body | body came back exactly 300 characters, valid JSON, in 2.1 s |
| `maxLength: 0` on the body | body came back empty, valid JSON |
| `pattern` and `maxLength` on the same field | both enforced, valid JSON |

So the body length is now part of the schema, derived from the repository:

- the subject's cap is `max(72, the repository's p90 subject length)`, the same
  bound the conformance check uses, so the schema and the check agree;
- the body's cap is zero when the repository's prose-body rate is below 0.35 — a
  body of length zero is not something the model is asked to avoid, it is
  something the model cannot produce;
- otherwise the cap is the repository's p90 body length in words, times six
  characters a word, bounded to 300–2000 characters.

This is the same principle as the subject pattern, applied to length: the
repository's habits are enforced by the grammar, and the model is left with
nothing to get wrong.

The effect on latency is large. The same prompt that took 25–30 s unbounded took
**1.7–2.1 s** with a capped body, because the grammar prunes the search space
instead of letting the model run to a repetition loop.

Every row generated before this change used a schema that permitted runaway
bodies, so **the whole run was discarded and restarted** with the corrected
schema. The first arm's partial results are not in the final numbers.








