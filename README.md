# sloth

A commit-message and release-changelog writer that runs entirely on your machine.

Sloth reads the repository's own history, works out how that repository writes its
commit messages, and asks a **local** open-weight model to write the words. The
model never decides the format, the type, the scope vocabulary or the version
number — those are computed from the repository's history by code. The model only
supplies wording.

Your unreleased code never leaves your machine. There is no per-commit fee. It works
offline once the model is on disk.

---

## Install

```sh
cargo install --path crates/sloth
```

You need a local model. [Ollama](https://ollama.com) is the easy path; any
OpenAI-compatible local server works.

```sh
ollama pull qwen3:0.6b
```

You do **not** have to start the server yourself. Sloth starts it if it is not
already running, tells you it is doing so, and leaves it running afterwards. If it
cannot start one, it says so and falls back to a deterministic message rather than
blocking your commit.

## Use

```sh
git add -A
sloth commit
```

Sloth shows the diffstat and a proposed message, then waits for one keypress:

| key | action |
|---|---|
| `y` | accept and commit |
| `e` | open `$EDITOR` on the message |
| `r` | regenerate, with a rotating nudge |
| `q` | cancel, nothing changes |

Nothing is committed or tagged without that keypress.

```sh
sloth release            # propose a version, write a changelog section, tag it
sloth release --dry-run  # show everything, change nothing
sloth prompt             # print the prompt, the schema and the mined style
sloth prompt --json      # the same, as JSON, without calling a model
```

Sloth **never pushes**. It prints the command for you to run.

## Configuration

No config file. Four environment variables:

| variable | default | meaning |
|---|---|---|
| `SLOTH_MODEL` | `qwen3:0.6b` | which model to talk to |
| `SLOTH_URL` | `http://127.0.0.1:11434` | the local model server |
| `SLOTH_STYLE` | *(mined)* | force a shape: `conventional`, `gitmoji`, `ticket`, `freeform` |
| `SLOTH_ALLOW_REMOTE` | unset | set to `1` to permit a non-loopback endpoint |

`SLOTH_ALLOW_REMOTE` exists because the privacy guarantee is only true for a local
endpoint. By default Sloth **refuses to run** against anything that is not
loopback, rather than quietly sending your diff to a remote service.

The default model is small on purpose. Writing a commit message needs no reasoning,
so reasoning is switched off explicitly: a model with thinking enabled and a
JSON-schema constraint returns an *empty* response that looks exactly like a broken
model. If you see `the model returned no content`, that is the first thing to check.

## How it works

1. **Mine the convention.** The last 300 non-merge commits are classified into
   shapes — Conventional Commits, gitmoji, ticket-prefixed or freeform — and the
   repository's own type vocabulary, scope vocabulary, subject-length distribution,
   pull-request-suffix habit and body structure are extracted from them. A
   repository that writes `## What?` / `## Why?` bodies gets told to.
2. **Build the digest.** The changed paths, a diffstat, and the diff with **no
   context lines** — the hunk headers still carry the enclosing function's name,
   which is the context a subject actually needs. Lockfiles, vendored code, build
   output and generated files are dropped.
3. **Never refuse a diff.** A three-thousand-line refactor is a normal commit. If
   the digest does not fit the context, Sloth raises the context window (one call,
   up to 32k) and if that is still not enough, it summarises the diff in pieces and
   combines the summaries. Either way you get a message, and Sloth tells you which
   it did: `diff was 61,000 lines; summarised in 14 pieces, then combined`.
4. **Constrain the shape.** The JSON schema sent to the model carries a `pattern`
   for the subject and a `maxLength` for both fields, generated from the mined
   convention. The model server compiles the schema into a grammar and masks
   every token that cannot continue a valid value, so a subject that breaks the
   repository's convention is *unreachable* rather than merely invalid, and a
   body the repository does not use is not something the model can write at all.
   The model chooses the words; the shape is not a matter of opinion. Bounding
   the body also stops a small model from running away in a repetition loop —
   on this laptop the same prompt took 25 s unbounded and 2 s bounded.
5. **Generate, then validate.** The model's output is checked against the
   repository's convention. If it fails, one repair call is made with the specific
   failures appended. If that fails too, a deterministic template is offered
   instead — Sloth never blocks your commit.
6. **Clean up what cannot be known.** A local tool cannot know a pull-request
   number, so any `(#1234)` the model invents is removed. A scope the repository
   has never used is flagged for you rather than rewritten, and so is a subject
   that names a file path.
7. **Ask.** Accept, edit, regenerate or cancel. One keypress.


The version bump is computed from the commits — a `!` marker or a
`BREAKING CHANGE:` body means major, `feat` means minor, `fix` means patch — and
the tag is re-rendered in whatever format the repository already uses
(`v1.2.3`, `1.2.3`, `pkg-v1.2.3`, `pkg@1.2.3`). The model writes the changelog
prose; it never writes the version.

## The benchmark

`bench/` holds a pre-registered benchmark that measures whether a small local
model is actually good enough, on 80 real commits from five real repositories.

- [`bench/PROTOCOL.md`](bench/PROTOCOL.md) — the rubric, the thresholds, the
  corpus, and every amendment made before the run
- [`bench/REPORT.md`](bench/REPORT.md) — the results
- `bench/cases.jsonl` — the captured commits and diffs
- `bench/run.py` — the harness; `bench/rate.py` and `bench/judge.py` — the rating
  harness; `bench/analyze.py` — the scoring

The thresholds were written down before a single model was run, and the file
records the defects found and fixed on the way there, so the result can be checked
rather than taken on trust.

## Layout

```
crates/sloth/     the CLI
  prompts/        the prompt template and the fallback schema, embedded in the binary
  src/
    git.rs        every git interaction, by shelling out to git
    style.rs      mining the repository's convention
    diff.rs       the staged-diff digest and the noise filter
    strategy.rs   choosing a level for a diff that will not fit in one context
    model.rs      the local model client
    msg.rs        message assembly, the JSON schema, and conformance checks
    release.rs    version bump, tag scheme, changelog
    server.rs     starting the local model server if it is not running
    ui.rs         the inline confirm, edit and regenerate flow
bench/            the benchmark, its protocol, its corpus and its results
```

## Status

The benchmark is the load-bearing part: it establishes which model is good enough
for which repository, and the honest answer is not the same for every repository.
`bench/REPORT.md` will hold the measured numbers, including where small models fail.

**The numbers do not exist yet.** The harness, the protocol and the corpus are
finished, and the first arm ran far enough to show that a 0.6B model can reach 96%
conformance against a repository's own convention — but the schema and the diff
handling changed underneath it afterwards, so those rows were discarded rather than
reported. Re-running the benchmark is the next step, and it is deliberately the
last thing before this is finished rather than the first.

Two things are still open: whether the model should be embedded (llama.cpp) rather
than reached over HTTP, and whether the benchmark should drive this binary directly
instead of reimplementing it in Python.


## License

MIT
