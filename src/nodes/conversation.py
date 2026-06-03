"""Conversational (meta) intent handling.

Some questions are not about ERP data at all but about the *conversation
itself* - e.g. "what was the last question I asked?" or "what did you just
say?". These are answered deterministically from short-term conversation
history (``state["messages"]``), never from the ERP backend and never from the
LLM, so the answer is always faithful and works even when the model is down.

This module is intentionally pure (no I/O, no deps) so it is trivially testable
and can be reused by both the planner (to set the intent) and the context
builder (to produce the answer).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Recall topics.
PREVIOUS_QUESTION = "previous_question"
PREVIOUS_ANSWER = "previous_answer"
HISTORY = "history"

# Trigger phrases per topic (English + Arabic). Checked as case-insensitive
# substrings. Order of evaluation matters: more specific topics first.
_TRIGGERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        PREVIOUS_ANSWER,
        (
            "what did you say", "what did you answer", "your last answer",
            "your previous answer", "what was your answer", "what was the answer",
            "repeat your answer", "ماذا قلت", "ماذا أجبت", "ماذا اجبت",
            "إجابتك السابقة", "اجابتك السابقة", "آخر إجابة", "اخر اجابة",
            "ما هي إجابتك", "ايش قلت", "شو قلت",
        ),
    ),
    (
        PREVIOUS_QUESTION,
        (
            "last question", "previous question", "what did i ask",
            "what was my question", "what i asked", "my last question",
            "earlier question", "the question i asked", "last thing i asked",
            "what was the last question", "السؤال السابق", "اخر سؤال",
            "آخر سؤال", "ماذا سألت", "ما الذي سألت", "ما الذى سألت",
            "سؤالي السابق", "سؤالي الاخير", "سؤالي الأخير", "ايش سألت",
            "شو سألت", "ما هو سؤالي",
        ),
    ),
    (
        HISTORY,
        (
            "what did we talk about", "what have we discussed",
            "what did we discuss", "conversation history", "chat history",
            "what have i asked", "everything i asked", "ماذا تحدثنا",
            "عن ماذا تحدثنا", "سجل المحادثة", "تاريخ المحادثة",
            "المحادثة السابقة", "ماذا سألتك", "كل أسئلتي",
        ),
    ),
)


def detect_recall_topic(question: str) -> str | None:
    """Return the recall topic for a meta-question, or ``None`` if not a recall."""
    lowered = (question or "").lower()
    for topic, phrases in _TRIGGERS:
        if any(phrase in lowered for phrase in phrases):
            return topic
    return None


def _contents(messages: Sequence[dict[str, Any]], role: str) -> list[str]:
    """Non-empty message contents for a given role, in order."""
    return [
        str(m.get("content", "")).strip()
        for m in messages
        if m.get("role") == role and str(m.get("content", "")).strip()
    ]


def answer_recall(
    messages: Sequence[dict[str, Any]], topic: str, language: str
) -> str:
    """Build a deterministic answer to a recall question from history.

    ``messages`` is expected to already include the *current* user turn as the
    last user message (the planner appends it), so it is excluded when looking
    for the "previous" question.
    """
    is_ar = str(language).startswith("ar")
    user_questions = _contents(messages, "user")
    assistant_answers = _contents(messages, "assistant")
    prior_questions = user_questions[:-1] if user_questions else []

    if topic == PREVIOUS_QUESTION:
        if not prior_questions:
            return (
                "لم تسألني أي سؤال قبل هذا." if is_ar
                else "You haven't asked me anything before this."
            )
        last = prior_questions[-1]
        return (
            f"سؤالك السابق كان: «{last}»" if is_ar
            else f'Your previous question was: "{last}"'
        )

    if topic == PREVIOUS_ANSWER:
        if not assistant_answers:
            return (
                "لم أقدّم أي إجابة بعد." if is_ar
                else "I haven't given any answer yet."
            )
        last = assistant_answers[-1]
        return (
            f"إجابتي السابقة كانت: «{last}»" if is_ar
            else f'My previous answer was: "{last}"'
        )

    # HISTORY (and any unknown topic) -> recent questions list.
    if not prior_questions:
        return (
            "لا يوجد سجل محادثة سابق بعد." if is_ar
            else "There's no earlier conversation yet."
        )
    recent = prior_questions[-5:]
    body = "\n".join(f"- {q}" for q in recent)
    header = "إليك أحدث أسئلتك:" if is_ar else "Here are your most recent questions:"
    return f"{header}\n{body}"
