# =============================================================================
# PH Agent Hub — What the turn handed the model (Issue #661)
# =============================================================================
# A session stores one user message, but the string the model actually receives
# is the contextualized one built by ``_maybe_summarize_and_build_context``, and
# MAF may further compact it in-run.  Until the turn records that string there
# is no way to tell a model failure from a message-fidelity failure from a
# session export.

# ``user_message_digest`` is the single definition of the six metrics fields
# that answer "what was the model given this turn?".  Head *and* tail are kept
# on purpose: the observed loss (Issue #661) was at the end of the message, so
# a head-only preview would have looked healthy three times.
#
# ``TurnInputPreservingCompaction`` is the matching guarantee: MAF's token
# budget fallback keeps only the *last* non-system group, so a tool-heavy turn
# can hand the model a conversation without the user's request while the
# transcript still shows it.  The turn's request is a few hundred characters;
# dropping it saves nothing and costs the turn its subject.
#
# Pure module: no settings, no I/O.
# =============================================================================

import hashlib

from agent_framework import (
    EXCLUDED_KEY,
    EXCLUDE_REASON_KEY,
    TokenBudgetComposedStrategy,
)

USER_MESSAGE_PREVIEW_CHARS = 200
"""Characters of the turn input kept as the ``head`` and ``tail`` previews.

Bounded on purpose: the digest rides inside the persisted (and exported)
``metrics`` part, so a 176 KB file-injected message must not enlarge the row.
"""


def user_message_digest(
    user_message: str | None,
    *,
    history_messages: int = 0,
    contextualized: bool = False,
) -> dict:
    """Describe the exact string a turn handed to the model (Issue #661).

    Args:
        user_message: The exact string passed to ``agent.run(...)`` for this
            turn — i.e. the string *after* contextualization, never the stored
            row.  ``None`` (a workflow checkpoint resume has no new user
            message) yields null digest fields.
        history_messages: How many stored messages rendered into the replayed
            ``[Previous conversation]`` block (``0`` when none did).
        contextualized: Whether the turn input was more than the user's
            message, i.e. whether a history block was prepended.

    Returns:
        A dict with exactly the six Issue #661 keys:
        ``user_message_chars``, ``user_message_sha256``, ``user_message_head``,
        ``user_message_tail``, ``history_messages``, ``contextualized``.

    Notes:
        * ``user_message_sha256`` is the hash of the *agent input*.  For a turn
          that replayed no history it therefore equals the hash of the stored
          message; for a contextualized turn it deliberately does not.
        * The previews are raw slices (no ellipsis marker) so that
          ``tail.endswith(<last bytes of the message>)`` is meaningful.
        * Encoding uses ``surrogatepass``: a lone surrogate pasted from a
          clipboard must never turn telemetry into a failed turn.
    """
    if user_message is None:
        chars: int | None = None
        sha256: str | None = None
        head: str | None = None
        tail: str | None = None
    else:
        chars = len(user_message)
        sha256 = hashlib.sha256(
            user_message.encode("utf-8", errors="surrogatepass")
        ).hexdigest()
        head = user_message[:USER_MESSAGE_PREVIEW_CHARS]
        tail = user_message[-USER_MESSAGE_PREVIEW_CHARS:]

    return {
        "user_message_chars": chars,
        "user_message_sha256": sha256,
        "user_message_head": head,
        "user_message_tail": tail,
        "history_messages": max(0, int(history_messages or 0)),
        "contextualized": bool(contextualized),
    }


class TurnInputPreservingCompaction(TokenBudgetComposedStrategy):
    """A compaction budget that can never exclude the turn's user message.

    Issue #661 — MAF applies the configured compaction strategy before model
    rounds and, when the composed strategies do not bring the conversation
    under its token budget, the built-in fallback excludes the oldest groups
    (keeping only the last non-system one, see
    ``_minimum_retained_group_ids`` in ``agent_framework._compaction``).  The
    first thing it therefore drops is the user's request: the model then
    reasons about tool results and its own earlier rounds with no subject in
    context, while the transcript still shows the request — the exact
    disagreement Issue #661 documents.

    The request group is re-included after MAF's pass.  It is a few hundred
    characters, so the prompt may sit marginally above the configured budget;
    that is the deliberate trade (fidelity over a few tokens).

    ``compacted_rounds`` counts the model rounds in which compaction changed
    the conversation at all, which is what the turn records as
    ``context_compacted``: a triager can then tell "the model lost the thread"
    from "the model never had the thread".
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.compacted_rounds = 0

    async def __call__(self, messages: list) -> bool:
        changed = await super().__call__(messages)
        if not changed:
            return False

        self.compacted_rounds += 1
        first_user = next(
            (m for m in messages if getattr(m, "role", "") == "user"),
            None,
        )
        if (
            first_user is not None
            and first_user.additional_properties.get(EXCLUDED_KEY)
        ):
            # Undo the budget fallback for the turn's request only.
            first_user.additional_properties[EXCLUDED_KEY] = False
            # The reason annotation is bookkeeping only, but leaving it set
            # would describe a message that is included again.
            first_user.additional_properties.pop(EXCLUDE_REASON_KEY, None)
        return True
