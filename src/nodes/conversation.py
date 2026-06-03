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

import re
from collections.abc import Sequence
from typing import Any

# Recall topics.
PREVIOUS_QUESTION = "previous_question"
PREVIOUS_ANSWER = "previous_answer"
HISTORY = "history"

# Small-talk topics (social/meta turns, no ERP/LLM involved).
GREETING = "greeting"
THANKS = "thanks"
GOODBYE = "goodbye"
HOW_ARE_YOU = "how_are_you"
CAPABILITIES = "capabilities"

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


# --- small talk -------------------------------------------------------------
# Multi-word phrases are matched as substrings (safe: unlikely to collide with a
# data question). Single words are matched as whole tokens only, so short
# greetings like "hi" are caught without misclassifying data questions that
# merely *contain* such a word (e.g. "...high availability...").
_SMALLTALK_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        CAPABILITIES,
        (
            "what can you do", "what can i ask", "what do you do", "what are you",
            "who are you", "how can you help", "how do you help", "how do i use you",
            "what can you help", "what kind of questions",
            "ماذا تستطيع", "ماذا يمكنك", "ماذا تفعل", "كيف تساعدني", "كيف يمكنك مساعدتي",
            "من انت", "من أنت", "ما الذي تستطيع", "ايش تقدر",
        ),
    ),
    (
        HOW_ARE_YOU,
        (
            "how are you", "how are u", "how's it going", "how is it going",
            "how do you do", "كيف حالك", "كيف الحال", "اخبارك", "أخبارك", "كيفك",
        ),
    ),
    (
        THANKS,
        (
            "thank you", "thank u", "thanks a lot", "many thanks", "much appreciated",
            "appreciate it", "شكرا لك", "شكراً لك", "جزيل الشكر", "يعطيك العافية",
        ),
    ),
    (
        GOODBYE,
        (
            "good bye", "goodbye", "see you", "see ya", "talk later", "catch you later",
            "مع السلامة", "الى اللقاء", "إلى اللقاء", "في امان الله", "في أمان الله",
        ),
    ),
    (
        GREETING,
        (
            "good morning", "good afternoon", "good evening", "good day",
            "hello there", "hi there", "السلام عليكم", "صباح الخير", "مساء الخير",
            "اهلا وسهلا", "أهلا وسهلا", "اهلا بك", "أهلا بك",
        ),
    ),
)

# Single-token triggers, checked only when the message is short.
_SMALLTALK_TOKENS: tuple[tuple[str, frozenset[str]], ...] = (
    (CAPABILITIES, frozenset({"help", "مساعدة", "ساعدني"})),
    (THANKS, frozenset({"thanks", "thx", "thnx", "ty", "شكرا", "شكراً", "مشكور", "تسلم"})),
    (GOODBYE, frozenset({"bye", "goodbye", "cya", "وداعا", "وداعاً", "باي"})),
    (
        GREETING,
        frozenset({
            "hi", "hello", "hey", "heya", "hiya", "yo", "hellow", "helo",
            "مرحبا", "مرحباً", "اهلا", "أهلا", "اهلين", "أهلين", "هاي", "سلام", "هلا",
        }),
    ),
)

# Messages longer than this (in tokens) are never classified by single-token
# triggers, so "hey, who manages Finance?" stays a DATA question.
_SMALLTALK_MAX_TOKENS = 4


def _normalize_smalltalk(text: str) -> str:
    """Lowercase and strip punctuation, keeping word chars and Arabic letters."""
    lowered = (text or "").lower()
    return re.sub(r"[^\w\s\u0600-\u06FF]", " ", lowered).strip()


def detect_smalltalk(question: str) -> str | None:
    """Return a small-talk topic (greeting/thanks/...) or ``None``.

    Order matters: more specific topics (capabilities, "how are you") are
    checked before plain greetings so "how are you" is not read as a greeting.
    """
    norm = _normalize_smalltalk(question)
    if not norm:
        return None

    # 1) Multi-word phrases are unambiguous wherever they appear.
    for topic, phrases in _SMALLTALK_PHRASES:
        if any(phrase in norm for phrase in phrases):
            return topic

    # 2) Single-word triggers only on short messages, matched as whole tokens.
    tokens = norm.split()
    if len(tokens) > _SMALLTALK_MAX_TOKENS:
        return None
    token_set = set(tokens)
    for topic, words in _SMALLTALK_TOKENS:
        if token_set & words:
            return topic
    return None


def _capability_hint(capabilities: Sequence[str] | None, is_ar: bool) -> str:
    """Render an optional, config-derived sentence about what can be queried.

    ``capabilities`` are facet business names from the registry, so this never
    invents data - it only describes which categories are available.
    """
    names = [str(c).strip() for c in (capabilities or []) if str(c).strip()]
    if not names:
        return ""
    joined = "، ".join(names) if is_ar else ", ".join(names)
    return (
        f" يمكنني الإجابة عن أسئلة حول: {joined}." if is_ar
        else f" I can answer questions about: {joined}."
    )


def answer_smalltalk(
    topic: str, language: str, capabilities: Sequence[str] | None = None
) -> str:
    """Build a deterministic, bilingual small-talk reply.

    ``capabilities`` (facet business names) are appended to greeting/capability
    replies to gently guide the user toward answerable questions.
    """
    is_ar = str(language).startswith("ar")
    hint = _capability_hint(capabilities, is_ar)

    if topic == THANKS:
        return (
            "على الرحب والسعة! هل هناك شيء آخر تود معرفته؟" if is_ar
            else "You're welcome! Is there anything else you'd like to know?"
        )
    if topic == GOODBYE:
        return (
            "إلى اللقاء! عُد في أي وقت تحتاج فيه إلى بيانات." if is_ar
            else "Goodbye! Come back anytime you need data."
        )
    if topic == HOW_ARE_YOU:
        base = (
            "بخير وجاهز للمساعدة." if is_ar
            else "I'm doing well and ready to help."
        )
        return f"{base}{hint}".strip()
    if topic == CAPABILITIES:
        if hint:
            tail = " مثال: «من يملك النظام ABC؟»." if is_ar else ' For example: "Who owns System ABC?".'
            base = (
                "أنا مساعدك للاستعلام عن البيانات." if is_ar
                else "I'm your data assistant."
            )
            return f"{base}{hint}{tail}".strip()
        return (
            "أنا مساعدك للاستعلام عن البيانات. عمّ تود أن تسأل؟" if is_ar
            else "I'm your data assistant. What would you like to ask about?"
        )

    # GREETING (and any unknown small-talk topic): greet and offer help.
    base = "مرحباً! أنا مساعدك للاستعلام عن البيانات." if is_ar else "Hello! I'm your data assistant."
    tail = " بماذا يمكنني مساعدتك؟" if is_ar else " How can I help you?"
    return f"{base}{hint}{tail}".strip()
