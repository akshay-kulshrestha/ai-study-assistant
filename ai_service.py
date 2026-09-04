"""Gemini-backed study features: summaries, quizzes, flashcards, and Q&A.

Talks to the Google Gemini API (https://ai.google.dev) over HTTPS. Requires
the GEMINI_API_KEY environment variable to be set before any of this will
work. Get a free key (no credit card required) at
https://aistudio.google.com.

This module previously talked to a locally running Ollama server -- that
required a separate always-running server process and CPU-bound generation
taking 30-65s per question. Unlike the interview simulator's ai_analyzer.py
(which could move to a fixed local question bank once it didn't need an
LLM anymore), this module can't do that: every feature here has to
generate content that's actually about whatever document the user
uploaded, which is inherently open-ended -- there's no fixed bank of
"the right quiz questions" for an arbitrary document. That's what makes a
cloud API the right tradeoff here specifically, even though the interview
simulator ultimately moved away from one.

Every generation call below is schema-forced via Gemini's
response_json_schema, which grammar-constrains the output to the declared
shape at generation time. This is more reliable than what the previous
Ollama version could get from a small local model's plain "format": "json"
mode, which is why the elaborate multi-strategy JSON recovery that version
needed (duplicate-key scanning, brace-matching, etc.) isn't needed here --
a schema-constrained cloud model doesn't produce that failure mode in the
first place. The remaining validation below (coercing/rejecting malformed
items) is defensive, not a routine necessity.
"""

import logging
import os
import re
import time
import json

from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

log = logging.getLogger("study_assistant")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")
# Tried in order if GEMINI_MODEL is unavailable to this account (404) or
# its free-tier daily quota is exhausted (429) -- both have been observed
# to happen with specific model versions/aliases independent of actual
# traffic, so falling back to a different model keeps the app usable
# rather than failing outright.
GEMINI_MODEL_FALLBACKS = [
    m.strip() for m in os.environ.get(
        "GEMINI_MODEL_FALLBACKS", "gemini-3.5-flash-lite,gemini-flash-latest"
    ).split(",") if m.strip()
]
SERVER_ERROR_RETRY_ATTEMPTS = int(os.environ.get("SERVER_ERROR_RETRY_ATTEMPTS", "2"))
SERVER_ERROR_RETRY_BACKOFF = float(os.environ.get("SERVER_ERROR_RETRY_BACKOFF", "2"))

# Gemini's context window is vastly larger than llama3.2's 4096 tokens (the
# reason the original cap was a tight 9000 characters) -- raised well past
# what any realistic study document needs, while still capping runaway
# cost/latency on a genuinely huge upload.
MAX_SOURCE_CHARS = int(os.environ.get("MAX_SOURCE_CHARS", "60000"))


class OllamaError(RuntimeError):
    """Raised when the AI backend can't be reached or returns bad output.

    Kept under its original name (from when this module talked to a local
    Ollama server) for compatibility with existing `except
    ai_service.OllamaError` handlers in app.py -- renaming it would mean
    touching that file too for no functional benefit.
    """


_client = None


def _get_client():
    global _client
    if _client is None:
        if not GEMINI_API_KEY:
            raise OllamaError(
                "GEMINI_API_KEY is not set. Set it in your environment "
                "(or .env locally) before starting the app. Get a free "
                "key at https://aistudio.google.com."
            )
        _client = genai.Client(api_key=GEMINI_API_KEY)
    return _client


def _gemini_generate(prompt, system, format_schema, max_output_tokens=1500):
    """Every call here is schema-forced (format_schema is always required,
    unlike the interview simulator's version which had an optional plain-
    JSON-mode path) -- every feature in this module needs a specific,
    predictable shape back, so there's no case where the looser mode is
    worth the reduced reliability."""

    client = _get_client()
    config = genai_types.GenerateContentConfig(
        system_instruction=system,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_json_schema=format_schema,
        # Explicitly setting thinking_budget=0 to disable "thinking" was
        # tried here, but rejected with 400 INVALID_ARGUMENT on
        # gemini-3.6-flash -- not every model accepts a zero budget (some
        # only allow reducing it to a model-specific minimum, not fully
        # disabling it), so rather than maintain a per-model compatibility
        # table, every call site below is given a generous max_output_tokens
        # instead, large enough to leave real headroom after whatever
        # thinking tokens the model uses on top of the actual output.
    )

    candidate_models = [GEMINI_MODEL] + GEMINI_MODEL_FALLBACKS
    last_error = None

    for model_name in candidate_models:
        max_attempts = SERVER_ERROR_RETRY_ATTEMPTS + 1

        for attempt in range(max_attempts):
            try:
                start = time.monotonic()
                response = client.models.generate_content(
                    model=model_name, contents=prompt, config=config,
                )
                elapsed = time.monotonic() - start
                log.info("Gemini call done in %.1fs (model=%s)", elapsed, model_name)
                return response.text or "", model_name

            except genai_errors.ServerError as exc:
                last_error = exc
                if attempt + 1 >= max_attempts:
                    log.warning(
                        "Gemini server error on %s after %d attempt(s), "
                        "trying next model if available: %s",
                        model_name, max_attempts, exc,
                    )
                    break
                backoff = SERVER_ERROR_RETRY_BACKOFF * (attempt + 1)
                log.warning(
                    "Gemini server error on %s, retrying in %.0fs "
                    "(attempt %d/%d): %s",
                    model_name, backoff, attempt + 1, max_attempts, exc,
                )
                time.sleep(backoff)

            except genai_errors.ClientError as exc:
                last_error = exc
                code = getattr(exc, "code", None)
                if code in (404, 429):
                    log.warning(
                        "Gemini model %s unavailable/exhausted (code=%s), "
                        "trying next model if available: %s",
                        model_name, code, exc,
                    )
                    break
                log.error("Gemini API request failed (code=%s): %s", code, exc)
                raise OllamaError(f"Gemini API request failed: {exc}") from exc

            except Exception as exc:
                log.error("Gemini API unreachable: %s", exc)
                raise OllamaError(
                    "Couldn't reach the Gemini API. Check your network "
                    "connection and that GEMINI_API_KEY is set correctly."
                ) from exc

    log.error("All Gemini models exhausted (%s), giving up: %s",
              ", ".join(candidate_models), last_error)
    raise OllamaError(f"Gemini API request failed: {last_error}") from last_error


def warm_up():
    try:
        _, model_used = _gemini_generate(
            'Reply with {"ok": true}',
            "Respond with strict JSON only.",
            format_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]},
            max_output_tokens=500,  # generous even for a trivial call -- thinking tokens can dominate a small budget
        )
        # Log whichever model actually served the request, not GEMINI_MODEL
        # unconditionally -- if the primary model is out of quota (a 429)
        # and a fallback model handled it instead, logging the configured
        # primary as "ready" is actively misleading for anyone watching
        # these logs to gauge quota health.
        log.info("Gemini API reachable and ready (model=%s).", model_used)
    except OllamaError as exc:
        log.warning("Warm-up failed (will retry on first real request): %s", exc)


def truncate_for_context(text):
    if len(text) <= MAX_SOURCE_CHARS:
        return text, False
    return text[:MAX_SOURCE_CHARS], True


# ============================================================
# Summary
# ============================================================

_SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


def summarize(text):
    source, truncated = truncate_for_context(text)
    system = (
        "You are a study assistant that writes clear, well-organized "
        "summaries of student notes. Write 4-8 sentences (or short "
        "bullet-style sentences separated by newlines) covering the main "
        "concepts, in plain language a student can quickly review."
    )
    prompt = f"Summarize these study notes:\n\n{source}"

    log.info("Summarizing text (%d chars, truncated=%s)", len(source), truncated)
    raw, _ = _gemini_generate(prompt, system, _SUMMARY_SCHEMA, max_output_tokens=3000)
    summary = _parse_field(raw, "summary")
    if not summary:
        raise OllamaError("Model returned an empty summary.")
    return summary


# ============================================================
# Quiz
# ============================================================
# No minItems/maxItems on the options array below -- that combination
# (nested array-length constraints inside an object inside another array)
# caused a 400 INVALID_ARGUMENT from the API with no more specific detail
# than "Request contains an invalid argument". The prompt's explicit
# "exactly 4 options" instruction plus _coerce_questions' own validation
# (which rejects anything that isn't exactly 4) enforce this instead.

_QUIZ_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "correct_index": {"type": "integer"},
                    "explanation": {"type": "string"},
                },
                "required": ["topic", "question", "options", "correct_index", "explanation"],
            },
        },
    },
    "required": ["questions"],
}


def generate_quiz(text, num_questions=5):
    source, truncated = truncate_for_context(text)
    system = (
        "You are a study assistant that writes multiple-choice quiz "
        "questions from student notes. Each question has exactly 4 "
        "options, \"correct_index\" is the 0-based index of the right "
        "one, \"explanation\" is a one-sentence reason the correct "
        "answer is right, and \"topic\" is a short (1-3 word) label for "
        "the specific concept the question tests (e.g. \"Photosynthesis\", "
        "\"Cell Membrane\") -- used as a tag shown above the question, so "
        "it must describe what THIS question is actually about, not the "
        "document as a whole. Base every question strictly on the "
        "provided notes -- never invent facts not present in them."
    )
    prompt = (
        f"Write exactly {num_questions} multiple-choice questions "
        f"covering these notes.\n\nNotes:\n{source}"
    )

    log.info("Generating %d-question quiz (%d chars, truncated=%s)",
             num_questions, len(source), truncated)

    for attempt in range(2):
        try:
            raw, _ = _gemini_generate(prompt, system, _QUIZ_SCHEMA, max_output_tokens=3000 + num_questions * 400)
            questions = _coerce_questions(_parse_field(raw, "questions", default=[]))
        except OllamaError as exc:
            log.warning("Quiz generation attempt %d/2 failed: %s", attempt + 1, exc)
            continue
        if questions:
            if len(questions) < num_questions:
                log.warning("Quiz generation returned only %d/%d requested questions",
                            len(questions), num_questions)
            return questions
        log.warning("Quiz generation attempt %d/2 returned no usable questions", attempt + 1)
    raise OllamaError("Model didn't return any usable quiz questions.")


def _coerce_questions(raw):
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "")).strip()
        question = str(item.get("question", "")).strip()
        options = item.get("options")
        correct_index = item.get("correct_index")
        explanation = str(item.get("explanation", "")).strip()
        # The schema no longer enforces exactly 4 options (removing
        # minItems/maxItems fixed a 400 INVALID_ARGUMENT from the API --
        # see the comment on _QUIZ_SCHEMA), so this now has to enforce it
        # itself: reject anything that isn't exactly 4, rather than
        # silently truncating/padding to fit a 4-option UI.
        if not question or not isinstance(options, list) or len(options) != 4:
            continue
        options = [str(o).strip() for o in options]
        try:
            correct_index = int(correct_index)
        except (TypeError, ValueError):
            continue
        if not (0 <= correct_index < len(options)):
            continue
        cleaned.append({
            "topic": topic, "question": question, "options": options,
            "correct_index": correct_index, "explanation": explanation,
        })
    return cleaned


# ============================================================
# Flashcards
# ============================================================

_FLASHCARDS_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "question": {"type": "string"},
                    "answer": {"type": "string"},
                },
                "required": ["topic", "question", "answer"],
            },
        },
    },
    "required": ["cards"],
}


def generate_flashcards(text, num_cards=8):
    source, truncated = truncate_for_context(text)
    system = (
        "You are a study assistant that writes flashcards from student "
        "notes. Each card's question is a short term or concept prompt "
        "phrased as a real question or instruction (e.g. \"Define "
        "mitochondria\" or \"What triggers osmosis?\"), never just the "
        "bare term with no verb. The answer is a concise 1-2 sentence "
        "definition or explanation. \"topic\" is a short (1-3 word) label "
        "for the broader subject area the card belongs to (e.g. \"Cell "
        "Biology\", \"Genetics\") -- used as a tag shown above the card. "
        "Base every card strictly on the provided notes."
    )
    prompt = (
        f"Write exactly {num_cards} flashcards covering these notes.\n\n"
        f"Notes:\n{source}"
    )

    log.info("Generating %d flashcards (%d chars, truncated=%s)",
             num_cards, len(source), truncated)

    for attempt in range(2):
        try:
            raw, _ = _gemini_generate(prompt, system, _FLASHCARDS_SCHEMA, max_output_tokens=3000 + num_cards * 200)
            cards = _coerce_flashcards(_parse_field(raw, "cards", default=[]))
        except OllamaError as exc:
            log.warning("Flashcard generation attempt %d/2 failed: %s", attempt + 1, exc)
            continue
        if cards:
            if len(cards) < num_cards:
                log.warning("Flashcard generation returned only %d/%d requested cards",
                            len(cards), num_cards)
            return cards
        log.warning("Flashcard generation attempt %d/2 returned no usable cards", attempt + 1)
    raise OllamaError("Model didn't return any usable flashcards.")


def _coerce_flashcards(raw):
    if not isinstance(raw, list):
        return []
    cleaned = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        topic = str(item.get("topic", "")).strip()
        question = str(item.get("question", "")).strip()
        answer = str(item.get("answer", "")).strip()
        if question and answer:
            cleaned.append({"topic": topic, "question": question, "answer": answer})
    return cleaned


# ============================================================
# Chat / Q&A
# ============================================================

_ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


def answer_question(document_text, question, history=None):
    source, truncated = truncate_for_context(document_text)
    system = (
        "You are a study assistant helping a student understand their "
        "own notes. Answer using ONLY the information in the provided "
        "notes -- if the notes don't cover something the student asks, "
        "say so rather than guessing. Keep answers focused and a few "
        "sentences long unless more detail is clearly needed."
    )
    history_text = ""
    if history:
        history_text = "\n\nPrior conversation:\n" + "\n".join(
            f"{'Student' if h['role'] == 'user' else 'Assistant'}: {h['content']}"
            for h in history[-6:]
        )
    prompt = f"Notes:\n{source}{history_text}\n\nStudent's question: {question}"

    log.info("Answering question about notes (%d chars, truncated=%s): %s",
             len(source), truncated, question[:80])
    raw, _ = _gemini_generate(prompt, system, _ANSWER_SCHEMA, max_output_tokens=3000)
    answer = _parse_field(raw, "answer")
    if not answer:
        raise OllamaError("Model returned an empty answer.")
    return answer


# ============================================================
# Shared JSON field parsing
# ============================================================

def _parse_field(raw_text, key, default=None):
    """Every _gemini_generate call is schema-forced, so raw_text is
    guaranteed syntactically valid JSON matching the declared schema --
    this is a plain parse, not the elaborate recovery the Ollama version
    needed for a small local model's less reliable output."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise OllamaError(f"Model returned malformed JSON: {raw_text[:200]!r}") from exc
    value = data.get(key, default)
    return value.strip() if isinstance(value, str) else value
