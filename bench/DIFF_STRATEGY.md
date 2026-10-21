# The diff strategy

Shared contract. The Rust CLI (`crates/sloth/`) and the Python benchmark harness
(`bench/`) implement this identically, because the benchmark is only meaningful
if it measures the prompt the tool actually sends. `bench/xcheck.py` compares the
two.

**Rule: Sloth never refuses a diff.** A three-thousand-line refactor is a normal
commit. Failing and telling the user to narrow the stage is not an acceptable
answer. The tool always produces a message, and always says what it did.

## Level A — the compressed digest (the common case)

The digest is:

1. the list of changed paths,
2. a diffstat: added and deleted line counts per path,
3. the diff, **compressed to hunk headers and changed lines only** — no context
   lines.

Compression is one git call: `git diff --cached --unified=0 --no-color`.

Hunk headers matter. `@@ -2135,6 +2135,9 @@ func (m *Model) Update(...)` carries
the enclosing function's name, which is the context a subject needs and which no
amount of context lines would carry better.

The current implementation sends `--unified=3`, which roughly triples the prompt
for the same information. Level A is the default and it is cheaper than what
ships today.

Estimate the prompt size as `chars / 3.5` — a deliberate over-estimate — and
compare it against the context budget.

**Budget:** `num_ctx` minus the system prompt, minus room for the output.

```
budget = num_ctx - system_prompt_tokens - 800
```

The 800 is room for the subject and a body.

If the digest fits the budget at the default `num_ctx` (8192), send it. One model
call. This is the fast path and the overwhelming majority of real commits.

## Level B — raise the context (one call, bigger window)

If the digest does not fit, raise `num_ctx` for that call, up to `MAX_NUM_CTX`
(32768), and check again.

This is still **one model call**. It is slower, because prefill dominates and
prefill scales with the context, but it is simpler and it sees everything.

Some models advertise a smaller maximum context than 32768. If the model's own
limit is lower, use the lower of the two. If the model cannot be asked, use
`num_ctx` as given.

Only raise the context when the digest genuinely needs it. Do not raise it
speculatively: a larger context is slower for every call.

## Level C — chunked summarisation (the safety net)

If the digest still does not fit at `MAX_NUM_CTX`, split it into chunks that each
fit the budget, and run a map phase followed by a reduce phase.

**Map phase.** For each chunk, one model call asking for a single line: what
changed in this piece. Keep the line short — a hard cap of 40 tokens per chunk,
which keeps the map phase cheap. The chunk includes the paths it covers and the
hunk headers it contains, so the summary can name things.

**Reduce phase.** One call over the per-chunk lines, asking for the commit
subject. The reduce input is the concatenation of the map lines, which is small
by construction, plus the file list and the diffstat.

The map phase must not be able to fail the run. If a chunk's call fails, use a
deterministic fallback line for that chunk — the chunk's paths and hunk headers
joined — and carry on. Level C must always terminate with a subject.

## What the user is told

The tool states which level it used, and how much it compressed. Never let the
user believe they read a full diff when they did not.

- Level A: nothing. This is the normal case and needs no commentary.
- Level B: `diff was 14,200 lines; sent all of it in a 32k context (about 40s).`
- Level C: `diff was 61,000 lines; summarised in 14 pieces, then combined.`

Level C's wording should say the diff was summarised, because it was.

## Interaction with the conformance checks

None. The strategy changes what the model sees, not how its answer is judged. A
message produced from a summarised diff is scored the same way as one produced
from a full diff, and the fidelity axis is what will show whether the
summarisation lost anything.

## Interaction with the benchmark

The protocol previously said an over-budget case was discarded and reported. That
rule is superseded: with this strategy there is no over-budget case. Every case
is answered, and the level used is recorded per row so the report can break the
results down by it.

## Numbers

| constant | value | why |
|---|---|---|
| `num_ctx` default | 8192 | explicit, because the server's default is small enough to truncate silently |
| `MAX_NUM_CTX` | 32768 | past this, prefill dominates and the wait is worse than chunking |
| `CHARS_PER_TOKEN` | 3.5 | a deliberate over-estimate, so the budget is never exceeded |
| output reserve | 800 tokens | room for the subject and a body |
| map-phase line cap | 40 tokens | keeps the map phase cheap; there are many chunks |
| diff context lines | 0 | `--unified=0`; the hunk header carries the function name |
