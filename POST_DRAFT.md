# Sloth: commit messages from a model that lives in the binary

I am slow at writing commit messages. Not the code — the one-line summary. In honor of International Sloth Day, I built the tool I wanted: stage your changes, run one command, and a message comes back that reads like your repository wrote it. The model runs on your machine, inside the process. Nothing leaves it.

## What I Built

Sloth is a small Rust CLI with two commands: `sloth commit` and `sloth release`. You stage your changes and run one command; it proposes a message, and nothing is committed or tagged without a keypress from you.

```sh
git add -A
sloth commit
```

One keypress: accept, edit in `$EDITOR`, regenerate, or cancel. `sloth release` computes the version bump from the commits, writes a changelog section, and creates an annotated tag. It never pushes — it prints the command for you to run.

The lazy payoff, measured in the only unit that matters: writing a conventional commit message by hand takes me about a minute of staring, plus a trip through `git log` to check how this repo formats things. Sloth does both in one command and one keypress. Across a normal day of commits, that is the difference between staying in flow and context-switching into changelog-brain a dozen times.

## Demo

Real terminal output from the shipped binary. First, what Sloth sees in a repository — the mined convention, dumped without calling any model:

```
$ sloth prompt --json
keys: ['schema', 'style', 'system', 'user', 'variables']
pattern: ^(feat)(\([a-z0-9._/-]+\))?!?: .{1,72}$
body maxLength: 0
style shape: conventional | scope_rate: 1.0
```

That pattern is generated from the repo's own history, and the model server compiles it into a grammar — a subject that breaks the convention is unreachable rather than merely invalid. Then the proposal:

```
$ sloth commit --dry-run
sloth: convention: conventional (high, 100% of 3 commits); prose bodies 0%, trailers 0%, scopes 100%

  src/lib.rs | +1 -1
  1 file(s) changed, +1 -1

--- proposed message ---
feat(core): update function f5 to f6
------------------------

dry run: nothing was committed.
```

## Code

https://github.com/ishaan-jindal/sloth — MIT licensed.

Rust, four direct dependencies, one embedded inference engine (llama.cpp via the `llama-cpp-2` crate, CPU-only). Default model is `qwen3:0.6b` — deliberately the smallest thing on the ladder. No server, no port, no network connection; the model file is memory-mapped, not uploaded.

## How I Built It

The whole design is one sentence: the model decides as little as possible.

Sloth reads the repository's last 300 non-merge commits and mines how that repository writes: Conventional Commits, gitmoji, ticket prefixes, or freeform, plus the actual type and scope vocabulary, subject-length habits, and body structure. The model never decides the format, the type, the scope vocabulary or the version number — those are computed from history by code.

Three things earned their place the hard way:

1. **The schema is a constraint, then a check.** A `pattern` on the subject and `maxLength` on both fields, enforced by the grammar compiler. But a grammar is a nudge, not a proof — escaped characters can slip past it, and an unsupported pattern silently degrades to a free string with one stderr warning. So the output is parsed and re-checked in code (full regex on the unescaped value, same length bound) before anything else happens, and a degraded grammar is a hard error, not a fallback.
2. **A local tool cannot know a pull-request number**, so any `(#1234)` the model invents is removed in code. Same for bodies a repo never writes: the schema cap is zero, which makes them unwritable rather than discouraged.
3. **A 3,000-line refactor is a normal commit**, so there is no "diff too big" error. The tool raises its context window, and past that, summarises the diff in pieces and combines them — telling you which it did.

I am not the first person here to make a machine write these. The thread I am joining includes [lazycommit](https://dev.to/mrnowshirwan/lazycommit-a-tiny-local-model-writes-my-commit-messages-so-i-dont-have-to-k4p), [Auto-generate Commit Messages with LLMs in Your Terminal](https://dev.to/hankchiutw/auto-generate-commit-messages-with-llms-in-your-terminal-1a43), and [Stop Writing Boring Commit Messages](https://dev.to/glemiu6/stop-writing-boring-commit-messages-let-a-local-ai-do-it-for-you-j58). Sloth's bet is different: when the tool determines everything deterministic, the model is left with the one thing it is actually asked to do, which is wording.

## Why Does Open Innovation Matter?

Every load-bearing piece is open: the weights (any GGUF works — nothing in the tool is tied to one lab's model), the inference engine (llama.cpp), the prompt, the schema, and the convention miner. That is not decoration, it is the mechanism. A closed per-token API would make "a commit message with zero marginal cost" impossible; a closed model would make "the grammar enforces your repo's format" unverifiable. And privacy is structural, not promised: there is no HTTP client in the binary and no endpoint to misconfigure, so unreleased code cannot leave the machine by construction, not by policy.

## Prize Categories

None — Sloth runs on open-weight models through llama.cpp with no partner technology involved, so this is an entry for the overall prize. The lazy payoff above is the whole pitch: a tool for the task every developer dreads, in honor of the slowest animal with the best ideas about energy conservation. 🦥

## What this post does not claim

I did not benchmark models against each other. The repo contains a benchmark harness with a pre-registered protocol and 80 real commits, and it was deliberately never run — so I am not telling you a 0.6B model is good enough. Try a bigger model and a smaller one and see which one you stop noticing. That difference — the point where you accept without reading — is the real benchmark, and it happens at your keyboard, not in my table.

---
*Disclosure: this post was written with AI assistance. The tool it describes was also built with AI assistance, which feels worth saying plainly.*
