# AI Study Assistant

Turn any PDF, DOCX, or TXT document into an interactive study guide. Upload your notes and get an AI-generated summary, a graded multiple-choice quiz, a flip-card flashcard deck, and a chat that answers questions grounded strictly in your document — no invented facts, no outside information.

## Features

- **Smart Summaries** — a structured overview of your document, generated on demand.
- **Interactive Quizzes** — auto-generated multiple-choice questions with instant grading, per-question explanations, topic tags, and an animated score visualization. Presented one question at a time with a progress bar; all questions are revealed for review once you submit.
- **Flip-Card Flashcards** — auto-generated term/definition cards with a flip animation, shuffle and reset controls, and dot-progress navigation.
- **Document Chat** — ask questions about your material and get answers grounded only in what's actually in the document, with the option to clear the conversation history at any time.
- **Progress Dashboard** — tracks quiz history and highlights weak topics (documents where your average score is below 60%) across everything you've uploaded.

## Tech Stack

- **Backend:** Python, Flask, SQLite (WAL mode, foreign keys enforced)
- **AI:** [Google Gemini API](https://ai.google.dev) via the `google-genai` SDK — schema-forced JSON generation with automatic model fallback and retry-with-backoff for resilience against rate limits and transient errors
- **Document parsing:** `pypdf` (PDF), `python-docx` (Word), built-in text handling with encoding fallback (TXT)
- **Frontend:** Server-rendered Jinja2 templates, vanilla JavaScript (no framework), custom CSS design system
- **Accessibility:** Full keyboard navigation, ARIA live regions, screen-reader-tested radio groups and tab panels

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/akshay-kulshrestha/ai-study-assistant.git
cd ai-study-assistant
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Get a Gemini API key

Go to [Google AI Studio](https://aistudio.google.com/apikey), sign in, and click **Create API key**. It's free — no credit card required for the free tier. The key will start with `AIzaSy...`.

### 4. Configure your environment

Copy the example environment file and fill in your key:

```bash
cp env.example .env
```

Open `.env` and set:

```
GEMINI_API_KEY=AIzaSy...your_actual_key_here
```

`.env` is loaded automatically at startup via `python-dotenv` — no need to set the variable manually in your shell. `.env` is excluded from version control by `.gitignore`; never commit it.

### 5. Run the app

```bash
python app.py
```

By default it runs on `http://127.0.0.1:5002`. Set the `PORT` environment variable to change it, or `FLASK_DEBUG=1` to enable debug mode.

## Project Structure

```
ai_study_assistant/
├── app.py                  # Flask routes and request handling
├── ai_service.py           # Gemini API integration (summaries, quizzes, flashcards, chat)
├── database.py             # SQLite schema and connection handling
├── document_parser.py      # Text extraction for PDF/DOCX/TXT
├── requirements.txt
├── env.example
├── static/
│   ├── css/style.css
│   └── js/                 # index, upload, library, study, quiz, flashcards
└── templates/
    ├── base.html
    ├── index.html           # Landing page
    ├── upload.html          # Upload page
    ├── library.html         # Document list + progress dashboard
    └── study.html           # Tabbed document view (Summary/Quiz/Flashcards/Chat)
```

## Notes

- Uploaded files are capped at 15MB and must be PDF, DOCX, or TXT.
- Very long documents are truncated to the first ~60,000 characters before being sent to the model (configurable via `MAX_SOURCE_CHARS`).
- If the primary Gemini model is rate-limited or unavailable, the app automatically falls back to alternate models (configurable via `GEMINI_MODEL_FALLBACKS`).
