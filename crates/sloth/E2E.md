# End-to-end transcript

Real runs of the built binary against a scratch repository. Recorded against the
earlier HTTP/Ollama backend; the current binary embeds the model instead, and the
CLI flow is unchanged. Kept as evidence that the tool works, and as a record of
the defects these runs exposed.

## The scratch repository

A throwaway git repo with eight commits, all `feat(core): ...`, so the tool had a
conventional history to mine.

```
feat(core): add function f1
feat(core): add function f2
... (8 commits)
```

## `sloth commit --dry-run`

```
$ sloth commit --dry-run
sloth: convention: conventional (high, 100% of 8 commits); prose bodies 0%, trailers 0%, scopes 100%

  src/helper.rs | +1 -0
  src/lib.rs | +1 -1
  2 file(s) changed, +2 -1

--- proposed message ---
feat(core): add helper functions to src/helper.rs and src/lib.rs

Add helper function `helper(x: u32) -> u32` to `src/helper.rs` and `f1() -> i32` to `src/lib.rs`.
------------------------

dry run: nothing was committed. Would run: git commit -F /tmp/sloth-...-commit-message.txt
```

The tool mined the convention, produced a message with the repository's own type
and scope, showed the diffstat, and committed nothing.

**Defect found:** the subject names file paths. The prompt says "Never mention file
paths", and the model ignored it. The tool now flags this rather than rewriting it
— the model chose those words from the diff, so it knows something the tool does
not.

## `sloth commit` — accept with one keypress

```
$ printf 'y\n' | sloth commit
  src/lib.rs | +1 -1
  1 file(s) changed, +1 -1

--- proposed message ---
fix(core): update function f5 to f6

fix(core): update function f5 to f6
------------------------

Accept [y], edit [e], regenerate [r], cancel [q]: [main 5d1cd30] fix(core): update function f5 to f6
 1 file changed, 1 insertion(+), 1 deletion(-)
committed.
```

**Defect found:** the body is a duplicate of the subject. A small model asked for
two fields will often fill both with the same string, and that duplicate ends up
in every commit. The tool now drops a body that equals the subject, and drops a
body entirely when the repository does not use commit bodies.

## `sloth release --dry-run`

```
$ sloth release --dry-run
sloth: tag scheme v1.2.3; latest v0.2.0; ignored 0 non-version tag(s)

Proposed release tag: v0.3.0
--- CHANGELOG.md section ---
## [0.3.0] - 2026-10-20

### Added

- feat(api): add function f5
- feat(core): add function f4

### Highlights

v0.3.0 introduces new features f5 and f4 in the API and core, respectively.

----------------------------

Would write /tmp/opencode/sloth-e2e/CHANGELOG.md
Would run: git tag -a v0.3.0 -m 'Release v0.3.0'
Then push with: git push origin v0.3.0
```

The tag scheme was inferred from the repository's existing tags, the bump was
computed from the commits (`feat` → minor → `v0.3.0`), the changelog was grouped
by type in code, and only the Highlights paragraph was written by the model.
Nothing was written and no tag was created.

## `sloth release` — accept

```
$ printf 'y\n' | sloth release
Accept release [y], cancel [q]: wrote /tmp/opencode/sloth-e2e/CHANGELOG.md
created annotated tag v0.3.0
never pushed by sloth; run: git push origin v0.3.0
```

The resulting `CHANGELOG.md`:

```markdown
# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-10-20

### Added

- feat(core): add function f7
- feat(api): add function f5
- feat(core): add function f4

### Fixed

- fix(core): update function f5 to f6

### Highlights

v0.3.0 introduces new features in the core and API, including adding functions f7, f5, and f4, and updating function f5 to f6.
```

The annotated tag was created. Nothing was pushed — the scratch repository had no
remote, and the tool prints the push command for the user to run.
