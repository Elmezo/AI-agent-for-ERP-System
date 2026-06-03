"""Tests for conversational (recall) intent handling."""

from __future__ import annotations

import pytest

from src.nodes.conversation import (
    CAPABILITIES,
    GOODBYE,
    GREETING,
    HISTORY,
    HOW_ARE_YOU,
    PREVIOUS_ANSWER,
    PREVIOUS_QUESTION,
    THANKS,
    answer_recall,
    answer_smalltalk,
    detect_recall_topic,
    detect_smalltalk,
)

# A conversation where the *current* turn (last user message) is the recall
# question itself, mirroring how the planner appends it before context builds.
_HISTORY_MESSAGES = [
    {"role": "user", "content": "Who manages the Finance Department?"},
    {"role": "assistant", "content": "Youssef Nabil"},
    {"role": "user", "content": "Who owns System ABC?"},
    {"role": "assistant", "content": "Ahmed Mohamed"},
    {"role": "user", "content": "what is the last question i asked you about?"},
]


def test_detect_previous_question_topics() -> None:
    assert detect_recall_topic("what is the last question i asked you about?") == PREVIOUS_QUESTION
    assert detect_recall_topic("ما هو سؤالي السابق؟") == PREVIOUS_QUESTION
    assert detect_recall_topic("اخر سؤال سألته") == PREVIOUS_QUESTION


def test_detect_previous_answer_topic() -> None:
    assert detect_recall_topic("what did you say?") == PREVIOUS_ANSWER
    assert detect_recall_topic("ماذا قلت لي؟") == PREVIOUS_ANSWER


def test_detect_history_topic() -> None:
    assert detect_recall_topic("what did we talk about?") == HISTORY


def test_non_recall_returns_none() -> None:
    assert detect_recall_topic("Who owns System ABC?") is None
    assert detect_recall_topic("How many employees are there?") is None


def test_answer_previous_question_excludes_current_turn() -> None:
    answer = answer_recall(_HISTORY_MESSAGES, PREVIOUS_QUESTION, "en")
    assert "Who owns System ABC?" in answer
    assert "last question i asked" not in answer  # current turn excluded


def test_answer_previous_answer() -> None:
    answer = answer_recall(_HISTORY_MESSAGES, PREVIOUS_ANSWER, "en")
    assert "Ahmed Mohamed" in answer


def test_answer_history_lists_prior_questions() -> None:
    answer = answer_recall(_HISTORY_MESSAGES, HISTORY, "en")
    assert "Who manages the Finance Department?" in answer
    assert "Who owns System ABC?" in answer


def test_answer_previous_question_when_no_history() -> None:
    msgs = [{"role": "user", "content": "what was my last question?"}]
    assert answer_recall(msgs, PREVIOUS_QUESTION, "en") == "You haven't asked me anything before this."
    assert answer_recall(msgs, PREVIOUS_QUESTION, "ar") == "لم تسألني أي سؤال قبل هذا."


# --- small talk -------------------------------------------------------------
@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("hi", GREETING),
        ("Hello!", GREETING),
        ("hey there", GREETING),
        ("good morning", GREETING),
        ("مرحبا", GREETING),
        ("السلام عليكم", GREETING),
        ("thanks", THANKS),
        ("thank you so much", THANKS),
        ("شكرا", THANKS),
        ("bye", GOODBYE),
        ("goodbye for now", GOODBYE),
        ("how are you?", HOW_ARE_YOU),
        ("كيف حالك", HOW_ARE_YOU),
        ("what can you do?", CAPABILITIES),
        ("help", CAPABILITIES),
        ("who are you", CAPABILITIES),
    ],
)
def test_detect_smalltalk_topics(text: str, topic: str) -> None:
    assert detect_smalltalk(text) == topic


@pytest.mark.parametrize(
    "text",
    [
        "Who owns System ABC?",
        "How many employees are there?",
        "Hey, who manages the Finance Department?",  # greeting word but a data question
        "I need help finding the owner of dataset X",  # 'help' inside a real question
        "",
    ],
)
def test_detect_smalltalk_ignores_data_questions(text: str) -> None:
    assert detect_smalltalk(text) is None


def test_smalltalk_does_not_collide_with_recall() -> None:
    # Recall phrases must not be swallowed by small-talk detection.
    assert detect_smalltalk("what did i ask before?") is None


def test_answer_smalltalk_greeting_includes_capabilities() -> None:
    answer = answer_smalltalk(GREETING, "en", capabilities=["People", "Systems"])
    assert answer.startswith("Hello!")
    assert "People, Systems" in answer


def test_answer_smalltalk_greeting_without_capabilities() -> None:
    answer = answer_smalltalk(GREETING, "en")
    assert "Hello!" in answer
    assert "How can I help you?" in answer


def test_answer_smalltalk_arabic_greeting() -> None:
    answer = answer_smalltalk(GREETING, "ar", capabilities=["الموظفون"])
    assert answer.startswith("مرحباً")
    assert "الموظفون" in answer


def test_answer_smalltalk_thanks_and_goodbye() -> None:
    assert "welcome" in answer_smalltalk(THANKS, "en").lower()
    assert "Goodbye" in answer_smalltalk(GOODBYE, "en")
