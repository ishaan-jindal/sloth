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

First install takes a few minutes: it compiles the embedded inference engine
from source, so you need a C++ toolchain (`cmake`, `gcc`/`clang`, `ninja` or
`make`). After that the binary is self-contained — model file aside.

You need a local model file. Point Sloth at a GGUF on disk, or name a model you
have already pulled — Sloth reads Ollama's local store directly, no server needed.

```sh
sloth --model qwen3:0.6b
```

There is no server, no port, and no network connection. The model runs inside
the Sloth process. If Sloth cannot find the model, it says so and stops rather
than guessing: pass a path to a `.gguf` file, or an Ollama model name such as
`qwen3:0.6b`. A model file that exists but fails to load falls back to a
deterministic message instead — Sloth never blocks your commit on a broken file,
only on a missing one.

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

No config file. Three environment variables, one flag:

| variable / flag | default | meaning |
|---|---|---|
| `SLOTH_MODEL` / `--model` | `qwen3:0.6b` | a path to a `.gguf` file, or an Ollama model name |
| `SLOTH_STYLE` | *(mined)* | force a shape: `conventional`, `gitmoji`, `ticket`, `freeform` |

The privacy guarantee is structural: there is no HTTP client in the binary and
no endpoint to misconfigure, so your diff cannot leave the machine. The model
file is memory-mapped, not uploaded anywhere.

The default model is small on purpose. Writing a commit message needs no reasoning,
so there is nothing to switch off: the embedded engine runs no thinking pass.
If you see `the model returned no content`, the model file itself is the first
thing to check.

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
4. **Constrain the shape, then check it.** The JSON schema sent to the model carries a
   `pattern` for the subject and a `maxLength` for both fields, generated from the mined
   convention. The grammar compiler turns the schema into a hard constraint on what the
   model can emit — but a grammar is a nudge, not a proof: escaped characters can slip
   past it, and an unsupported pattern degrades to a free string. So the model's output
   is parsed and re-checked in code (full regex on the unescaped value, same length
   bound) before anything else happens. If that check fails, one repair call is made
   with the specific failures appended. If that fails too, a deterministic template is
   offered instead — Sloth never blocks your commit on a bad generation.
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
    model.rs      the embedded model: resolution, grammar, generation
    msg.rs        message assembly, the JSON schema, and conformance checks
    release.rs    version bump, tag scheme, changelog
    ui.rs         the inline confirm, edit and regenerate flow
bench/            the benchmark, its protocol, its corpus and its results
```

## Status

Shipped as v0.1.0: `sloth commit`, `sloth release` and `sloth prompt`, with the
model embedded — no server, no port, no network.

`bench/` holds the benchmark harness, protocol and corpus (80 real commits from
five repositories). It is finished scaffolding with no results: the benchmark was
deliberately never run, so this project makes no claim about which model is good
enough — only about the mechanism that makes a small model worth trying. If the
numbers ever get measured, `bench/REPORT.md` is where they go.

Known issues with the embedded backend, recorded rather than fixed:
- Grammar-constrained generation runs ~35% slower than the same file through
  Ollama with a schema (15–17 vs 25.6 tok/s); the user-visible cost is ~1.6 s
  per one-shot commit. Threading differs between the two measurements, so the
  cause is not isolated.
- `commit` hard-errors on a model that resolves to nothing, while `release`
  warns and proceeds (highlights are decoration; the message is the product).
  That asymmetry is intended — do not "fix" it without deciding which side moves.


## License

MIT
