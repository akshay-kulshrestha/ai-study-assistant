import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv

# Must run before `import ai_service` below -- that module reads
# GEMINI_API_KEY into a module-level constant at import time, so if
# load_dotenv() ran after the import, .env values would arrive too late to
# matter and the app would behave as if the key were never set. An actual
# `set`/`export` in the real environment still takes precedence over .env,
# since load_dotenv() only fills in variables that aren't already set.
load_dotenv()

from flask import Flask, g, jsonify, render_template, request
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.utils import secure_filename

import ai_service
import database
import document_parser

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["MAX_CONTENT_LENGTH"] = document_parser.MAX_FILE_SIZE_BYTES
log = logging.getLogger("study_assistant")

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")


def get_db():
    if "db" not in g:
        g.db = database.get_connection()
    return g.db


@app.teardown_appcontext
def close_db(exception):
    db = g.pop("db", None)
    if db is not None:
        db.close()


@app.errorhandler(HTTPException)
def handle_http_exception(exc):
    """Werkzeug HTTPExceptions (404, 405, 413, etc.) render as Flask's
    default HTML error page unless handled -- fine for page routes, but
    every /api/ route promises JSON, and the frontend's fetch() calls parse
    the response as JSON. Without this, exceeding MAX_CONTENT_LENGTH (an
    oversized upload) raises a 413 that returns an HTML page, which breaks
    res.json() on the client instead of surfacing a real error message --
    the frontend falls back to a generic "something went wrong" instead of
    telling the user their file was too big.
    """
    if request.path.startswith("/api/"):
        message = exc.description or exc.name or "Request failed."
        if isinstance(exc, RequestEntityTooLarge):
            max_mb = document_parser.MAX_FILE_SIZE_BYTES // (1024 * 1024)
            message = f"That file is too large. Please upload something under {max_mb}MB."
        return jsonify({"error": message}), exc.code
    return exc


@app.errorhandler(Exception)
def handle_unexpected_error(exc):
    log.exception("Unhandled error on %s", request.path)
    if request.path.startswith("/api/"):
        return jsonify({"error": "Something went wrong on the server. Please try again."}), 500
    return "<h1>Something went wrong</h1><p>Please go back and try again.</p>", 500


def _now():
    return datetime.now(timezone.utc).isoformat()


def _parse_count_param(data, key, default, min_val, max_val):
    """Parse and clamp an integer request param, falling back to `default`
    on anything unparseable (missing, null, non-numeric string, etc.)
    instead of letting int() raise -- a bad/empty value in the count field
    would otherwise surface as a generic 500 rather than just using a
    sensible default."""
    raw = data.get(key, default)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning("Invalid %s=%r, falling back to default %d", key, raw, default)
        value = default
    return max(min_val, min(max_val, value))


# ------------------------------------------------------------------ pages ---

@app.route("/")
def home():
    return render_template("index.html", current_page="home")


@app.route("/upload")
def upload_page():
    return render_template("upload.html", current_page="upload")


@app.route("/library")
def library_page():
    db = get_db()
    documents = db.execute("SELECT * FROM documents ORDER BY uploaded_at DESC").fetchall()
    documents_by_id = {d["id"]: dict(d) for d in documents}

    attempts = [
        database.attempt_to_dict(a)
        for a in db.execute("SELECT * FROM quiz_attempts ORDER BY completed_at DESC").fetchall()
    ]

    # Per-document stats for the library cards (quizzes taken, best score,
    # last studied) and the aggregate stats for the Progress Dashboard tab
    # are both derived from the same quiz_attempts rows, so one pass builds
    # both instead of querying twice.
    per_document = {}
    for a in attempts:
        bucket = per_document.setdefault(
            a["document_id"], {"attempts": 0, "score_sum": 0, "total_sum": 0, "best_pct": None, "last_studied": None}
        )
        bucket["attempts"] += 1
        bucket["score_sum"] += a["score"]
        bucket["total_sum"] += a["total"]
        pct = round(a["score"] / a["total"] * 100) if a["total"] else 0
        if bucket["best_pct"] is None or pct > bucket["best_pct"]:
            bucket["best_pct"] = pct
        if bucket["last_studied"] is None or a["completed_at"] > bucket["last_studied"]:
            bucket["last_studied"] = a["completed_at"]

    doc_cards = []
    for d in documents:
        doc = database.document_to_dict(d)
        stats = per_document.get(doc["id"], {})
        doc["quiz_count"] = stats.get("attempts", 0)
        doc["best_pct"] = stats.get("best_pct")
        doc["last_studied"] = stats.get("last_studied")
        doc_cards.append(doc)

    topic_stats = []
    for doc_id, stats in per_document.items():
        doc = documents_by_id.get(doc_id)
        if not doc or stats["total_sum"] == 0:
            continue
        pct = round(stats["score_sum"] / stats["total_sum"] * 100)
        topic_stats.append({
            "document_id": doc_id,
            "name": doc["original_name"],
            "attempts": stats["attempts"],
            "percentage": pct,
        })
    topic_stats.sort(key=lambda t: t["percentage"])

    total_attempts = len(attempts)
    total_score = sum(a["score"] for a in attempts)
    total_questions = sum(a["total"] for a in attempts)
    overall_pct = round(total_score / total_questions * 100) if total_questions else None

    return render_template(
        "library.html",
        doc_cards=doc_cards,
        documents_by_id=documents_by_id,
        attempts=attempts,
        weak_areas=[t for t in topic_stats if t["percentage"] < 60][:5],
        total_attempts=total_attempts,
        overall_pct=overall_pct,
        current_page="library",
    )


@app.route("/study/<int:document_id>")
def study_page(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return "Document not found", 404
    chat = db.execute(
        "SELECT * FROM chat_messages WHERE document_id = ? ORDER BY id", (document_id,)
    ).fetchall()
    # A document can have more than one quiz/flashcard set generated over
    # time (e.g. regenerated with a different question count) -- the tabbed
    # view only has room to show one, so use the most recent.
    quiz_row = db.execute(
        "SELECT * FROM quizzes WHERE document_id = ? ORDER BY id DESC LIMIT 1", (document_id,)
    ).fetchone()
    flashcards_row = db.execute(
        "SELECT * FROM flashcard_sets WHERE document_id = ? ORDER BY id DESC LIMIT 1", (document_id,)
    ).fetchone()
    return render_template(
        "study.html",
        document=database.document_to_dict(doc),
        chat_messages=[dict(c) for c in chat],
        quiz=database.quiz_to_dict(quiz_row) if quiz_row else None,
        flashcard_set=database.flashcards_to_dict(flashcards_row) if flashcards_row else None,
        current_page="study",
    )


@app.route("/quiz/<int:quiz_id>")
def quiz_page(quiz_id):
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if quiz is None:
        return "Quiz not found", 404
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (quiz["document_id"],)).fetchone()
    return render_template(
        "quiz.html",
        quiz=database.quiz_to_dict(quiz),
        document=database.document_to_dict(doc),
        current_page="study",
    )


@app.route("/flashcards/<int:set_id>")
def flashcards_page(set_id):
    db = get_db()
    fc = db.execute("SELECT * FROM flashcard_sets WHERE id = ?", (set_id,)).fetchone()
    if fc is None:
        return "Flashcard set not found", 404
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (fc["document_id"],)).fetchone()
    return render_template(
        "flashcards.html",
        flashcard_set=database.flashcards_to_dict(fc),
        document=database.document_to_dict(doc),
        current_page="study",
    )


@app.route("/results")
def results_page():
    db = get_db()
    documents = {d["id"]: dict(d) for d in db.execute("SELECT * FROM documents").fetchall()}
    attempts = [
        database.attempt_to_dict(a)
        for a in db.execute("SELECT * FROM quiz_attempts ORDER BY completed_at DESC").fetchall()
    ]

    per_document = {}
    for a in attempts:
        bucket = per_document.setdefault(a["document_id"], {"attempts": 0, "score_sum": 0, "total_sum": 0})
        bucket["attempts"] += 1
        bucket["score_sum"] += a["score"]
        bucket["total_sum"] += a["total"]

    topic_stats = []
    for doc_id, stats in per_document.items():
        doc = documents.get(doc_id)
        if not doc or stats["total_sum"] == 0:
            continue
        pct = round(stats["score_sum"] / stats["total_sum"] * 100)
        topic_stats.append({
            "document_id": doc_id,
            "name": doc["original_name"],
            "attempts": stats["attempts"],
            "percentage": pct,
        })
    topic_stats.sort(key=lambda t: t["percentage"])

    total_attempts = len(attempts)
    total_score = sum(a["score"] for a in attempts)
    total_questions = sum(a["total"] for a in attempts)
    overall_pct = round(total_score / total_questions * 100) if total_questions else None

    return render_template(
        "results.html",
        documents=documents,
        attempts=attempts,
        topic_stats=topic_stats,
        weak_areas=[t for t in topic_stats if t["percentage"] < 60][:5],
        total_attempts=total_attempts,
        overall_pct=overall_pct,
        current_page="results",
    )


# -------------------------------------------------------------------- api ---

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file was uploaded."}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "No file was selected."}), 400
    if not document_parser.allowed_file(file.filename):
        return jsonify({"error": "Only PDF, DOCX, and TXT files are supported."}), 400

    original_name = secure_filename(file.filename)
    file_type = document_parser.file_type_from_name(original_name)
    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    stored_path = os.path.join(UPLOAD_DIR, stored_name)
    file.save(stored_path)

    log.info("Uploaded file: %s -> %s", original_name, stored_name)
    try:
        text = document_parser.extract_text(stored_path, file_type)
    except document_parser.ParseError as exc:
        os.remove(stored_path)
        log.warning("Failed to parse upload %s: %s", original_name, exc)
        return jsonify({"error": str(exc)}), 400

    word_count = len(text.split())
    db = get_db()
    cursor = db.execute(
        """
        INSERT INTO documents (original_name, stored_name, file_type, extracted_text, word_count, uploaded_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (original_name, stored_name, file_type, text, word_count, _now()),
    )
    db.commit()
    document_id = cursor.lastrowid
    log.info("Document %d stored (%d words)", document_id, word_count)

    return jsonify({"document_id": document_id}), 201


@app.route("/api/document/<int:document_id>", methods=["DELETE"])
def api_delete_document(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return jsonify({"error": "Document not found"}), 404
    stored_path = os.path.join(UPLOAD_DIR, doc["stored_name"])
    if os.path.exists(stored_path):
        os.remove(stored_path)
    db.execute("DELETE FROM documents WHERE id = ?", (document_id,))
    db.commit()
    log.info("Deleted document %d", document_id)
    return jsonify({"ok": True})


@app.route("/api/document/<int:document_id>/summarize", methods=["POST"])
def api_summarize(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return jsonify({"error": "Document not found"}), 404

    if doc["summary"]:
        return jsonify({"summary": doc["summary"]})

    try:
        summary = ai_service.summarize(doc["extracted_text"])
    except ai_service.OllamaError as exc:
        log.error("Document %d: summarize failed: %s", document_id, exc)
        return jsonify({"error": str(exc)}), 502

    db.execute("UPDATE documents SET summary = ? WHERE id = ?", (summary, document_id))
    db.commit()
    return jsonify({"summary": summary})


@app.route("/api/document/<int:document_id>/quiz", methods=["POST"])
def api_generate_quiz(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return jsonify({"error": "Document not found"}), 404

    data = request.get_json(silent=True) or {}
    num_questions = _parse_count_param(data, "num_questions", default=5, min_val=3, max_val=10)

    try:
        questions = ai_service.generate_quiz(doc["extracted_text"], num_questions)
    except ai_service.OllamaError as exc:
        log.error("Document %d: quiz generation failed: %s", document_id, exc)
        return jsonify({"error": str(exc)}), 502

    cursor = db.execute(
        "INSERT INTO quizzes (document_id, questions, created_at) VALUES (?, ?, ?)",
        (document_id, json.dumps(questions), _now()),
    )
    db.commit()
    return jsonify({"quiz_id": cursor.lastrowid}), 201


@app.route("/api/quiz/<int:quiz_id>/submit", methods=["POST"])
def api_submit_quiz(quiz_id):
    db = get_db()
    quiz = db.execute("SELECT * FROM quizzes WHERE id = ?", (quiz_id,)).fetchone()
    if quiz is None:
        return jsonify({"error": "Quiz not found"}), 404

    data = request.get_json(silent=True) or {}
    answers = data.get("answers")
    if not isinstance(answers, list):
        return jsonify({"error": "Missing answers."}), 400

    questions = json.loads(quiz["questions"])
    results = []
    score = 0
    for i, q in enumerate(questions):
        selected = answers[i] if i < len(answers) else None
        is_correct = selected == q["correct_index"]
        if is_correct:
            score += 1
        results.append({
            "question": q["question"],
            "options": q["options"],
            "selected_index": selected,
            "correct_index": q["correct_index"],
            "is_correct": is_correct,
            "explanation": q.get("explanation", ""),
        })

    db.execute(
        """
        INSERT INTO quiz_attempts (quiz_id, document_id, results, score, total, completed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (quiz_id, quiz["document_id"], json.dumps(results), score, len(questions), _now()),
    )
    db.commit()
    log.info("Quiz %d submitted: %d/%d", quiz_id, score, len(questions))

    return jsonify({"results": results, "score": score, "total": len(questions)})


@app.route("/api/document/<int:document_id>/flashcards", methods=["POST"])
def api_generate_flashcards(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return jsonify({"error": "Document not found"}), 404

    data = request.get_json(silent=True) or {}
    num_cards = _parse_count_param(data, "num_cards", default=8, min_val=4, max_val=15)

    try:
        cards = ai_service.generate_flashcards(doc["extracted_text"], num_cards)
    except ai_service.OllamaError as exc:
        log.error("Document %d: flashcard generation failed: %s", document_id, exc)
        return jsonify({"error": str(exc)}), 502

    cursor = db.execute(
        "INSERT INTO flashcard_sets (document_id, cards, created_at) VALUES (?, ?, ?)",
        (document_id, json.dumps(cards), _now()),
    )
    db.commit()
    return jsonify({"set_id": cursor.lastrowid}), 201


@app.route("/api/document/<int:document_id>/chat", methods=["POST"])
def api_chat(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return jsonify({"error": "Document not found"}), 404

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    if not question:
        return jsonify({"error": "Please enter a question."}), 400

    history_rows = db.execute(
        "SELECT role, content FROM chat_messages WHERE document_id = ? ORDER BY id", (document_id,)
    ).fetchall()
    history = [dict(r) for r in history_rows]

    try:
        answer = ai_service.answer_question(doc["extracted_text"], question, history)
    except ai_service.OllamaError as exc:
        log.error("Document %d: chat answer failed: %s", document_id, exc)
        return jsonify({"error": str(exc)}), 502

    now = _now()
    db.execute(
        "INSERT INTO chat_messages (document_id, role, content, created_at) VALUES (?, 'user', ?, ?)",
        (document_id, question, now),
    )
    db.execute(
        "INSERT INTO chat_messages (document_id, role, content, created_at) VALUES (?, 'assistant', ?, ?)",
        (document_id, answer, _now()),
    )
    db.commit()

    return jsonify({"answer": answer})


@app.route("/api/document/<int:document_id>/chat", methods=["DELETE"])
def api_clear_chat(document_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    if doc is None:
        return jsonify({"error": "Document not found"}), 404

    db.execute("DELETE FROM chat_messages WHERE document_id = ?", (document_id,))
    db.commit()
    log.info("Cleared chat history for document %d", document_id)
    return jsonify({"ok": True})


@app.route("/api/warmup")
def api_warmup():
    ai_service.warm_up()
    return jsonify({"ok": True})


database.init_db()
os.makedirs(UPLOAD_DIR, exist_ok=True)

threading.Thread(target=ai_service.warm_up, daemon=True).start()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5002))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app.run(host="0.0.0.0", debug=debug, port=port, threaded=True)
