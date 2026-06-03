"""Tests for conversational (recall) intent handling."""

from __future__ import annotations

from src.nodes.conversation import (
    EXPLANATION,
    HISTORY,
    PREVIOUS_ANSWER,
    PREVIOUS_QUESTION,
    answer_recall,
    detect_recall_topic,
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


def test_detect_explanation_topic() -> None:
    # The exact follow-up that previously triggered a web search + hallucination.
    assert detect_recall_topic("حسبتها ازاي") == EXPLANATION
    assert detect_recall_topic("كيف حسبت هذا الرقم؟") == EXPLANATION
    assert detect_recall_topic("How did you calculate that?") == EXPLANATION
    assert detect_recall_topic("where did you get that number") == EXPLANATION


def test_non_recall_returns_none() -> None:
    assert detect_recall_topic("Who owns System ABC?") is None
    assert detect_recall_topic("How many employees are there?") is None
    # Bare "explain"/"why" must stay a normal (data) question, not recall.
    assert detect_recall_topic("explain the ERP Modernization project") is None
    assert detect_recall_topic("ما متوسط ميزانية المشاريع؟") is None


def test_answer_previous_question_excludes_current_turn() -> None:
    answer = answer_recall(_HISTORY_MESSAGES, PREVIOUS_QUESTION, "en")
    assert "Who owns System ABC?" in answer
    assert "last question i asked" not in answer  # current turn excluded


def test_answer_previous_answer() -> None:
    answer = answer_recall(_HISTORY_MESSAGES, PREVIOUS_ANSWER, "en")
    assert "Ahmed Mohamed" in answer


def test_answer_explanation_restates_prior_answer_without_inventing() -> None:
    msgs = [
        {"role": "user", "content": "ما متوسط ميزانية المشاريع؟"},
        {"role": "assistant", "content": "متوسط budget: 186,666.67"},
        {"role": "user", "content": "حسبتها ازاي"},
    ]
    answer = answer_recall(msgs, EXPLANATION, "ar")
    # Restates the real, previously-computed figure and its source; no fabrication.
    assert "186,666.67" in answer
    assert "النظام" in answer


def test_answer_history_lists_prior_questions() -> None:
    answer = answer_recall(_HISTORY_MESSAGES, HISTORY, "en")
    assert "Who manages the Finance Department?" in answer
    assert "Who owns System ABC?" in answer


def test_answer_previous_question_when_no_history() -> None:
    msgs = [{"role": "user", "content": "what was my last question?"}]
    assert answer_recall(msgs, PREVIOUS_QUESTION, "en") == "You haven't asked me anything before this."
    assert answer_recall(msgs, PREVIOUS_QUESTION, "ar") == "لم تسألني أي سؤال قبل هذا."
