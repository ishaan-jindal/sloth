#!/usr/bin/env python3
"""Build a per-repository JSON schema for the commit subject.

The `pattern` on the subject field is not a post-hoc check. Ollama compiles the
JSON schema into a grammar and masks every token that cannot continue a valid
string, so a subject that breaks the repository's convention is *unreachable*.
The model supplies the words; the schema decides the shape.

Measured on this machine: with a plain schema every model tested dropped the
`type(scope):` prefix, because nothing required it. With a patterned schema all of
them produced a conforming prefix, and generation was faster, because the grammar
prunes the search space.

Grammar-compiler constraints learned the hard way (see PROTOCOL.md A5):
  - a top-level alternation of alternatives works: `(feat|fix|chore)`
  - a NESTED alternation inside a group is silently accepted but not enforced
  - a negated character class (`[^.]`) corrupts generation entirely
  - non-capturing groups and `?` quantifiers are fine
"""

import re

# Grammar forms that are known to compile and enforce correctly.
MAX_TYPES = 12
LENGTH_CEILING = 72
BODY_USAGE_THRESHOLD = 0.35
BODY_MIN = 300
BODY_MAX = 2000
CHARS_PER_WORD = 6


def _types_alternation(style):
    vocab = style.get("type_vocab") or {}
    if not vocab:
        return "[a-z]+"
    types = sorted(vocab, key=lambda k: (-vocab[k], k))[:MAX_TYPES]
    return "|".join(re.escape(t) for t in types)


def subject_pattern(style):
    """A regex that a conforming subject of this repository must match."""
    shape = (style.get("shape") or "freeform").lower()
    if shape == "conventional":
        # type(scope)!: subject   - scope and the breaking marker are both optional
        return rf"^({_types_alternation(style)})(\([a-z0-9._/-]+\))?!?: .+$"
    if shape == "gitmoji":
        return r"^:[a-z0-9_+-]+: .+$"
    if shape == "ticket":
        return r"^[A-Z][A-Z0-9]+-[0-9]+:? .+$"
    # freeform: one line, at most the conventional 72 characters
    return r"^.{1,72}$"


def subject_max_length(style):
    """The longest subject this repository writes, in characters.

    The same bound the conformance check uses, so the schema and the check
    agree instead of one being stricter than the other.
    """
    p90 = int(((style.get("subject_len") or {}).get("p90")) or 0)
    return max(LENGTH_CEILING, p90)


def body_max_length(style):
    """How long a body may be, in characters.

    Zero when the repository does not use commit bodies. The grammar compiler
    enforces `maxLength`, so a body of length zero is not something the model
    is asked to avoid - it is something the model cannot produce.
    """
    usage = float(style.get("body_usage") or 0.0)
    if usage < BODY_USAGE_THRESHOLD:
        return 0
    p90 = int(((style.get("body_words") or {}).get("p90")) or 0)
    if p90 <= 0:
        return 1200
    return max(BODY_MIN, min(BODY_MAX, p90 * CHARS_PER_WORD))


def build_schema(style):
    """The JSON schema sent to Ollama as `format` for a given repository."""
    return {
        "type": "object",
        "properties": {
            "subject": {
                "type": "string",
                "pattern": subject_pattern(style),
                "maxLength": subject_max_length(style),
                "description": "The single commit subject line, matching this repository's convention.",
            },
            "body": {
                "type": "string",
                "maxLength": body_max_length(style),
                "description": "Optional commit body. Empty string if this repository does not use commit bodies.",
            },
        },
        "required": ["subject", "body"],
        "additionalProperties": False,
    }


def format_rule(style):
    """One sentence stating the prefix requirement, for the prompt.

    The schema enforces it; this states it, so the model does not spend its
    attention discovering the constraint the hard way.
    """
    shape = (style.get("shape") or "freeform").lower()
    if shape == "conventional":
        vocab = style.get("type_vocab") or {}
        types = sorted(vocab, key=lambda k: (-vocab[k], k))[:MAX_TYPES]
        if types:
            listed = ", ".join(f"`{t}`" for t in types)
            return (
                f"The subject MUST begin with one of these type prefixes, followed by a colon: "
                f"{listed}. A subject without that prefix is invalid. "
                "Optionally put a scope in parentheses after the type, like `feat(tui):`."
            )
        return (
            "The subject MUST begin with a lowercase type word followed by a colon, like `feat: `. "
            "A subject without that prefix is invalid."
        )
    if shape == "gitmoji":
        return (
            "The subject MUST begin with a gitmoji code and a colon, like `:sparkles: `. "
            "A subject without one is invalid."
        )
    if shape == "ticket":
        return (
            "The subject MUST begin with a ticket reference such as `PROJ-123`, followed by a colon. "
            "A subject without one is invalid."
        )
    return "Write a single plain subject line, at most 72 characters, with no trailing period."
