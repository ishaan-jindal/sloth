# Sloth: commit messages from a model that lives in the binary

I am slow at writing commit messages. Not the code — the one-line summary. So I
built the tool I wanted: stage your changes, run one command, and a message comes
back that reads like your repository wrote it. The model runs on your machine,
inside the process. Nothing leaves it.

## What it is

Sloth is a small Rust CLI with two commands: `sloth commit` and `sloth release`.
You stage your changes and run one command; it proposes a message, and nothing is
committed or tagged without a keypress from you.

```sh
git add -A
sloth commit
```

One keypress: accept, edit in `$EDITOR`, regenerate, or cancel. `sloth release`
computes the version bump from the commits, writes a changelog section, and
creates an annotated tag. It never pushes — it prints the command for you to run.

I am not the first person here to make a machine write these. The thread I am
joining includes [lazycommit](https://dev.to/mrnowshirwan/lazycommit-a-tiny-local-model-writes-my-commit-messages-so-i-dont-have-to-k4p),
[Auto-generate Commit Messages with LLMs in Your Terminal](https://dev.to/hankchiutw/auto-generate-commit-messages-with-llms-in-your-terminal-1a43),
and [Stop Writing Boring Commit Messages](https://dev.to/glemiu6/stop-writing-boring-commit-messages-let-a-local-ai-do-it-for-you-j58).
Sloth's bet is different: the model should decide as little as possible.

## Local means local

There is no server, no port, and no network connection. The open-weight model
runs embedded in the binary through llama.cpp, straight from a GGUF file — a path
on disk, or a model you already pulled. Your unreleased code never leaves your
machine. There is no per-commit fee, and it works offline once the model file is
on disk.

The default model is deliberately tiny (`qwen3:0.6b`). Writing a commit message
needs no reasoning, and the tool is built around that: the model only supplies
wording.

## The model decides as little as possible

This is the whole design. Sloth reads the repository's history — the last 300
non-merge commits — and mines how that repository writes: Conventional Commits,
gitmoji, ticket prefixes, or freeform, plus the actual type and scope vocabulary,
subject-length habits, and body structure.

The model never decides the format, the type, the scope vocabulary or the version
number. Those are computed from history by code. What the model gets is a prompt
built from the digest (changed paths, a diffstat, and the diff with no context
lines — the hunk headers carry the function names, which is the context a subject
needs) and a JSON schema generated from the mined convention.

The schema is a constraint, not a suggestion: a `pattern` on the subject and a
`maxLength` on both fields, enforced by the grammar compiler — a subject that
breaks the repo's convention is unreachable rather than merely invalid. And
because a grammar is a nudge rather than a proof (escaped characters can slip
past it), the output is parsed and re-checked in code before anything else
happens. If that fails, one repair call with the specific failures appended; if
that fails, a deterministic template. Sloth never blocks your commit.

Two details that earned their place: a local tool cannot know a pull-request
number, so any `(#1234)` the model invents is removed in code. And a 3,000-line
refactor is a normal commit, so there is no "diff too big" error — the tool
raises its context window, and past that, summarises the diff in pieces and
combines them, telling you which it did.

## What this post does not claim

I did not benchmark this. The repo contains a benchmark harness with a
pre-registered protocol and 80 real commits, and it was deliberately never run —
so I am not telling you a 0.6B model is good enough. I am telling you the
mechanism that makes it worth trying: when the tool determines everything
deterministic (format, vocabulary, version, length), the model is left with the
one thing it is actually asked to do, which is wording.

Try it with a bigger model and a smaller one and see which one you stop noticing.
That difference — the point where you accept without reading — is the real
benchmark, and it happens at your keyboard, not in my table.

## Try it

```sh
cargo install --path crates/sloth
sloth --model /path/to/model.gguf commit
```

First install takes a few minutes (it compiles the inference engine; bring
cmake and a C++ compiler). The repo is linked below.

The repo: https://github.com/ishaan-jindal/sloth — MIT licensed.


---
*Disclosure: this post was written with AI assistance. The tool it describes was
also built with AI assistance, which feels worth saying plainly.*
