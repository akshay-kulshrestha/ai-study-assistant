(() => {
    const { document_id: documentId } = JSON.parse(document.getElementById("document-data").textContent);

    const errorBox = document.getElementById("error");
    errorBox.setAttribute("aria-live", "assertive");

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.remove("hidden");
        errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    // A non-2xx response isn't guaranteed to be JSON (a proxy/500 page can
    // return HTML), so don't let a failed res.json() surface a raw parse
    // error to the user.
    async function parseJsonSafe(res) {
        try {
            return await res.json();
        } catch {
            throw new Error("Something went wrong talking to the server. Please try again.");
        }
    }

    // Reads a count field and validates it's a real number within the
    // field's own min/max before it's sent. The generate buttons are
    // type="button", not a form submit, so the browser's own min/max
    // validation never runs on click -- this enforces it manually, and
    // guards against parseInt("") -> NaN -> JSON.stringify -> null.
    function readCount(inputId, label) {
        const input = document.getElementById(inputId);
        const value = parseInt(input.value, 10);
        const min = parseInt(input.min, 10);
        const max = parseInt(input.max, 10);
        const lo = Number.isFinite(min) ? min : 1;
        const hi = Number.isFinite(max) ? max : Infinity;
        if (!Number.isFinite(value) || value < lo || value > hi) {
            const range = Number.isFinite(max) ? `between ${lo} and ${hi}` : `at least ${lo}`;
            showError(`Please enter a valid ${label} (${range}).`);
            return null;
        }
        return value;
    }

    // Cloud generation is normally fast, but a model-availability fallback
    // or retry-with-backoff (see ai_service.py) can occasionally take
    // longer -- show elapsed time plus a staged reassurance message and an
    // indeterminate progress animation instead of a fixed, often-wrong ETA.
    function startGeneratingTimer(panel) {
        const elapsedEl = panel.querySelector(".elapsed-text");
        let elapsed = 0;
        elapsedEl.textContent = "0s elapsed";
        const handle = setInterval(() => {
            elapsed += 1;
            let note = "";
            if (elapsed >= 90) note = " -- taking longer than usual, but still working. Thanks for your patience.";
            else if (elapsed >= 30) note = " -- still working...";
            elapsedEl.textContent = `${elapsed}s elapsed${note}`;
        }, 1000);
        return () => clearInterval(handle);
    }

    // ------------------------------------------------------------------ tabs
    const tabs = [...document.querySelectorAll('[role="tab"]')];

    function activateTab(tab, { focus = true } = {}) {
        tabs.forEach(t => {
            const selected = t === tab;
            t.classList.toggle("active", selected);
            t.setAttribute("aria-selected", String(selected));
            t.tabIndex = selected ? 0 : -1;
            document.getElementById(t.getAttribute("aria-controls")).hidden = !selected;
        });
        if (focus) tab.focus();
    }

    tabs.forEach((tab, i) => {
        tab.addEventListener("click", () => activateTab(tab));
        tab.addEventListener("keydown", (e) => {
            if (e.key === "ArrowRight" || e.key === "ArrowLeft") {
                e.preventDefault();
                const next = e.key === "ArrowRight"
                    ? tabs[(i + 1) % tabs.length]
                    : tabs[(i - 1 + tabs.length) % tabs.length];
                activateTab(next);
            }
        });
    });

    // Restore the active tab after a reload (e.g. right after generating a
    // quiz/flashcard set, which reloads the page so the server renders the
    // freshly generated content) instead of always resetting to Summary.
    const hashTab = document.getElementById(`tab-${(location.hash || "").slice(1)}`);
    if (hashTab) activateTab(hashTab, { focus: false });

    // ------------------------------------------------------------- summary
    const summarizeBtn = document.getElementById("summarize-btn");
    if (summarizeBtn) {
        summarizeBtn.addEventListener("click", async () => {
            const emptyEl = document.getElementById("summary-empty");
            const loadingEl = document.getElementById("summary-loading");
            const textEl = document.getElementById("summary-text");

            summarizeBtn.disabled = true;
            emptyEl.classList.add("hidden");
            loadingEl.classList.remove("hidden");

            try {
                const res = await fetch(`/api/document/${documentId}/summarize`, { method: "POST" });
                const data = await parseJsonSafe(res);
                if (!res.ok) throw new Error(data.error || "Couldn't generate a summary.");
                textEl.textContent = data.summary;
                textEl.classList.remove("hidden");
                loadingEl.classList.add("hidden");
                summarizeBtn.remove();
            } catch (err) {
                loadingEl.classList.add("hidden");
                emptyEl.classList.remove("hidden");
                summarizeBtn.disabled = false;
                showError(err.message);
            }
        });
    }

    const toggleTextBtn = document.getElementById("toggle-text-btn");
    const extractedText = document.getElementById("extracted-text");
    toggleTextBtn.addEventListener("click", () => {
        const expanded = extractedText.classList.toggle("expanded");
        toggleTextBtn.setAttribute("aria-expanded", String(expanded));
        toggleTextBtn.innerHTML = expanded
            ? 'Collapse<i data-lucide="chevron-up" class="icon" aria-hidden="true" style="width:15px;height:15px;"></i>'
            : 'Expand<i data-lucide="chevron-down" class="icon" aria-hidden="true" style="width:15px;height:15px;"></i>';
        if (window.lucide) lucide.createIcons();
    });

    // ------------------------------------------------------------------ quiz
    const quizBtn = document.getElementById("quiz-btn");
    if (quizBtn) {
        const quizGenerating = document.getElementById("quiz-generating");
        quizBtn.addEventListener("click", async () => {
            const numQuestions = readCount("quiz-count", "number of questions");
            if (numQuestions === null) return;

            quizBtn.classList.add("hidden");
            quizGenerating.classList.remove("hidden");
            const stopTimer = startGeneratingTimer(quizGenerating);

            try {
                const res = await fetch(`/api/document/${documentId}/quiz`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ num_questions: numQuestions }),
                });
                const data = await parseJsonSafe(res);
                if (!res.ok) throw new Error(data.error || "Couldn't generate a quiz.");
                stopTimer();
                // The freshly generated quiz needs the taking-UI, which is
                // rendered server-side from quiz.questions -- reloading is
                // simpler and more robust than duplicating that render logic
                // in JS. The hash tells the reloaded page which tab to land
                // on instead of resetting to Summary.
                location.hash = "quiz";
                window.location.reload();
            } catch (err) {
                stopTimer();
                quizGenerating.classList.add("hidden");
                quizBtn.classList.remove("hidden");
                showError(err.message);
            }
        });
    }

    const quizForm = document.getElementById("quiz-form");
    if (quizForm) {
        const questionCards = [...quizForm.querySelectorAll(".quiz-question")];
        const total = questionCards.length;
        const progressHeader = document.getElementById("quiz-progress-header");
        const progressLabel = document.getElementById("quiz-progress-label");
        const progressPct = document.getElementById("quiz-progress-pct");
        const progressFill = document.getElementById("quiz-progress-fill");
        let current = 0;

        function renderQuizNav() {
            questionCards.forEach((card, i) => { card.hidden = i !== current; });
            const pct = Math.round(((current + 1) / total) * 100);
            progressLabel.textContent = `Question ${current + 1} of ${total}`;
            progressPct.textContent = `${pct}%`;
            progressFill.style.width = `${pct}%`;
        }

        quizForm.querySelectorAll(".q-next").forEach(btn => {
            btn.addEventListener("click", () => {
                if (current < total - 1) { current += 1; renderQuizNav(); }
            });
        });
        quizForm.querySelectorAll(".q-prev").forEach(btn => {
            btn.addEventListener("click", () => {
                if (current > 0) { current -= 1; renderQuizNav(); }
            });
        });

        quizForm.querySelectorAll(".q-option input").forEach(input => {
            input.addEventListener("change", () => {
                const card = input.closest(".quiz-question");
                card.querySelectorAll(".q-option").forEach(o => o.classList.remove("selected"));
                input.closest(".q-option").classList.add("selected");
            });
        });

        const { quiz_id: quizId } = JSON.parse(document.getElementById("quiz-data").textContent);
        const submitBtn = document.getElementById("submit-quiz-btn");
        const resultCard = document.getElementById("quiz-result");

        function buildScoreRing(container, score, maxScore, size, stroke) {
            const r = (size - stroke) / 2;
            const c = 2 * Math.PI * r;
            const ratio = maxScore > 0 ? Math.max(0, Math.min(1, score / maxScore)) : 0;
            const offset = c * (1 - ratio);
            const pct = ratio * 100;
            const color = pct >= 80 ? "#16a34a" : pct >= 60 ? "#2563eb" : pct >= 40 ? "#f59e0b" : "#dc2626";
            container.style.width = `${size}px`;
            container.style.height = `${size}px`;
            container.setAttribute("role", "img");
            container.setAttribute("aria-label", `Score: ${Math.round(pct)} percent`);
            container.innerHTML = `
                <svg width="${size}" height="${size}" aria-hidden="true">
                    <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="var(--ring-track)" stroke-width="${stroke}"></circle>
                    <circle class="ring-progress" cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="${stroke}"
                        stroke-dasharray="${c}" stroke-dashoffset="${c}" stroke-linecap="round"
                        style="transition: stroke-dashoffset .8s ease-out"></circle>
                </svg>
                <div class="center">
                    <span class="val" style="color:${color};font-size:${size * 0.24}px;">${Math.round(pct)}%</span>
                </div>`;
            const progressCircle = container.querySelector(".ring-progress");
            requestAnimationFrame(() => {
                requestAnimationFrame(() => { progressCircle.style.strokeDashoffset = String(offset); });
            });
        }

        quizForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            errorBox.classList.add("hidden");

            const answers = questionCards.map((card, i) => {
                const selected = quizForm.querySelector(`input[name="q${i}"]:checked`);
                return selected ? parseInt(selected.value, 10) : null;
            });

            if (answers.some(a => a === null)) {
                showError("Please answer every question before submitting.");
                return;
            }

            submitBtn.disabled = true;
            submitBtn.innerHTML = '<i data-lucide="loader-circle" class="icon" style="width:16px;height:16px;animation:spin 1s linear infinite;"></i>Grading...';
            if (window.lucide) lucide.createIcons();

            try {
                const res = await fetch(`/api/quiz/${quizId}/submit`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ answers }),
                });
                const data = await parseJsonSafe(res);
                if (!res.ok) throw new Error(data.error || "Couldn't submit the quiz.");
                renderResults(data.results, data.score, data.total);
            } catch (err) {
                submitBtn.disabled = false;
                submitBtn.innerHTML = '<i data-lucide="check-circle-2" class="icon" style="width:16px;height:16px;"></i>Submit Quiz';
                if (window.lucide) lucide.createIcons();
                showError(err.message);
            }
        });

        function renderResults(results, score, total) {
            results.forEach((r, i) => {
                const card = questionCards[i];
                card.querySelectorAll(`input[name="q${i}"]`).forEach(input => (input.disabled = true));

                const options = card.querySelectorAll(".q-option");
                options.forEach((opt, idx) => {
                    if (idx === r.correct_index) opt.classList.add("correct");
                    if (idx === r.selected_index && !r.is_correct) opt.classList.add("incorrect");
                });

                const feedback = card.querySelector(".q-feedback");
                feedback.classList.remove("hidden");
                feedback.classList.add(r.is_correct ? "correct" : "incorrect");
                feedback.setAttribute("aria-live", "polite");
                // r.explanation is AI-generated text and must never be inserted
                // as raw HTML -- innerHTML below is only for the hardcoded,
                // safe icon markup; the dynamic text goes through textContent.
                feedback.innerHTML = `<i data-lucide="${r.is_correct ? 'check-circle-2' : 'x-circle'}" class="icon"></i><span></span>`;
                feedback.querySelector("span").textContent =
                    `${r.is_correct ? "Correct! " : "Incorrect. "}${r.explanation || ""}`;
            });

            // Reveal every question for review now that grading is done,
            // instead of leaving the one-at-a-time nav in place.
            questionCards.forEach(card => { card.hidden = false; });
            quizForm.querySelectorAll(".q-nav").forEach(el => { el.hidden = true; });
            if (progressHeader) progressHeader.hidden = true;

            resultCard.classList.remove("hidden");
            buildScoreRing(document.getElementById("quiz-score-ring"), score, total, 120, 10);
            const titleEl = document.getElementById("quiz-result-title");
            titleEl.textContent = `${score} / ${total} correct`;
            if (window.lucide) lucide.createIcons();
            resultCard.scrollIntoView({ behavior: "smooth", block: "center" });
            titleEl.focus();
        }

        renderQuizNav();
    }

    // ----------------------------------------------------------- flashcards
    const flashcardsBtn = document.getElementById("flashcards-btn");
    if (flashcardsBtn) {
        const flashcardsGenerating = document.getElementById("flashcards-generating");
        flashcardsBtn.addEventListener("click", async () => {
            const numCards = readCount("card-count", "number of cards");
            if (numCards === null) return;

            flashcardsBtn.classList.add("hidden");
            flashcardsGenerating.classList.remove("hidden");
            const stopTimer = startGeneratingTimer(flashcardsGenerating);

            try {
                const res = await fetch(`/api/document/${documentId}/flashcards`, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ num_cards: numCards }),
                });
                const data = await parseJsonSafe(res);
                if (!res.ok) throw new Error(data.error || "Couldn't generate flashcards.");
                stopTimer();
                location.hash = "flashcards";
                window.location.reload();
            } catch (err) {
                stopTimer();
                flashcardsGenerating.classList.add("hidden");
                flashcardsBtn.classList.remove("hidden");
                showError(err.message);
            }
        });
    }

    const flashcardEl = document.getElementById("flashcard");
    if (flashcardEl) {
        const originalCards = JSON.parse(document.getElementById("flashcards-data").textContent);
        let cards = originalCards.slice();
        let index = 0;

        const questionEl = document.getElementById("fc-question");
        const answerEl = document.getElementById("fc-answer");
        const topicEl = document.getElementById("fc-topic");
        const counterEl = document.getElementById("card-counter");
        const dotsEl = document.getElementById("fc-dots");
        const prevBtn = document.getElementById("fc-prev");
        const nextBtn = document.getElementById("fc-next");
        const shuffleBtn = document.getElementById("fc-shuffle");
        const resetBtn = document.getElementById("fc-reset");
        const frontFace = flashcardEl.querySelector(".flashcard-front");
        const backFace = flashcardEl.querySelector(".flashcard-back");

        flashcardEl.setAttribute("role", "button");
        flashcardEl.setAttribute("tabindex", "0");
        flashcardEl.setAttribute("aria-pressed", "false");
        flashcardEl.setAttribute("aria-label", "Flashcard, press Enter or Space to flip");
        counterEl.setAttribute("aria-live", "polite");

        function isTypingTarget(el) {
            if (!el) return false;
            const tag = el.tagName;
            return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
        }

        function syncFaceVisibility(flipped) {
            // backface-visibility:hidden only hides the non-visible face
            // visually -- without this, a screen reader reads the question
            // and the answer at once, defeating the point of a flip card.
            frontFace.setAttribute("aria-hidden", String(flipped));
            backFace.setAttribute("aria-hidden", String(!flipped));
        }

        function flip() {
            const flipped = flashcardEl.classList.toggle("flipped");
            flashcardEl.setAttribute("aria-pressed", String(flipped));
            syncFaceVisibility(flipped);
        }

        function renderDots() {
            dotsEl.innerHTML = cards.map((_, i) =>
                `<span class="fc-dot${i === index ? ' active' : ''}"></span>`
            ).join("");
        }

        function render() {
            flashcardEl.classList.remove("flipped");
            flashcardEl.setAttribute("aria-pressed", "false");
            syncFaceVisibility(false);
            const card = cards[index];
            questionEl.textContent = card.question;
            answerEl.textContent = card.answer;
            if (card.topic) {
                topicEl.textContent = card.topic;
                topicEl.classList.remove("hidden");
            } else {
                topicEl.classList.add("hidden");
            }
            counterEl.textContent = `${cards.length} cards \u00b7 Card ${index + 1} of ${cards.length}`;
            prevBtn.disabled = index === 0;
            nextBtn.disabled = false;
            nextBtn.innerHTML = index === cards.length - 1
                ? 'Restart<i data-lucide="rotate-ccw" class="icon" style="width:16px;height:16px;"></i>'
                : 'Next<i data-lucide="chevron-right" class="icon" style="width:16px;height:16px;"></i>';
            renderDots();
            if (window.lucide) lucide.createIcons();
        }

        flashcardEl.addEventListener("click", flip);
        flashcardEl.addEventListener("keydown", (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                flip();
            }
        });

        prevBtn.addEventListener("click", () => {
            if (index > 0) { index -= 1; render(); }
        });
        nextBtn.addEventListener("click", () => {
            index = index === cards.length - 1 ? 0 : index + 1;
            render();
        });

        shuffleBtn.addEventListener("click", () => {
            // Fisher-Yates shuffle of a copy -- reset can always restore the
            // original order from `originalCards`.
            const shuffled = cards.slice();
            for (let i = shuffled.length - 1; i > 0; i--) {
                const j = Math.floor(Math.random() * (i + 1));
                [shuffled[i], shuffled[j]] = [shuffled[j], shuffled[i]];
            }
            cards = shuffled;
            index = 0;
            render();
        });

        resetBtn.addEventListener("click", () => {
            cards = originalCards.slice();
            index = 0;
            render();
        });

        document.addEventListener("keydown", (e) => {
            if (document.getElementById("panel-flashcards").hidden) return;
            if (!cards.length || isTypingTarget(document.activeElement)) return;
            if (e.key === "ArrowRight") nextBtn.click();
            if (e.key === "ArrowLeft") prevBtn.click();
        });

        render();
    }

    // ------------------------------------------------------------------ chat
    const chatLog = document.getElementById("chat-log");
    const chatInput = document.getElementById("chat-input");
    const chatSendBtn = document.getElementById("chat-send-btn");
    const chatClearBtn = document.getElementById("chat-clear-btn");
    chatLog.setAttribute("aria-live", "polite");

    const CHAT_EMPTY_HTML = `
        <div class="chat-empty-state" id="chat-empty">
            <span class="icon-box grad-violet-blue" style="margin:0 auto 14px;"><i data-lucide="message-circle" class="icon" aria-hidden="true"></i></span>
            <h3 style="margin:0 0 6px;font-size:1rem;">Ask anything about your document</h3>
            <p class="muted" style="margin:0 0 18px;">I'll search through the content and answer based only on what's in the text.</p>
            <div class="chat-suggestions">
                <button type="button" class="chat-suggestion">What is the main topic of this document?</button>
                <button type="button" class="chat-suggestion">Can you summarize the key concepts?</button>
                <button type="button" class="chat-suggestion">What are the most important takeaways?</button>
            </div>
        </div>`;

    function wireSuggestionButtons() {
        chatLog.querySelectorAll(".chat-suggestion").forEach(btn => {
            btn.addEventListener("click", () => sendChat(btn.textContent));
        });
    }

    function appendMessage(role, content) {
        const emptyMsg = document.getElementById("chat-empty");
        if (emptyMsg) emptyMsg.remove();
        chatClearBtn.classList.remove("hidden");
        const div = document.createElement("div");
        div.className = `chat-msg ${role}`;
        div.innerHTML = `<span class="chat-role">${role === "user" ? "You" : "AI"}</span><p></p>`;
        div.querySelector("p").textContent = content;
        chatLog.appendChild(div);
        chatLog.scrollTop = chatLog.scrollHeight;
        return div;
    }

    let sending = false;

    async function sendChat(questionOverride) {
        if (sending) return;
        const question = (questionOverride ?? chatInput.value).trim();
        if (!question) return;

        sending = true;
        chatInput.value = "";
        chatInput.disabled = true;
        chatSendBtn.disabled = true;

        appendMessage("user", question);
        const thinkingEl = appendMessage("assistant", "Thinking...");
        thinkingEl.classList.add("thinking");

        try {
            const res = await fetch(`/api/document/${documentId}/chat`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ question }),
            });
            const data = await parseJsonSafe(res);
            if (!res.ok) throw new Error(data.error || "Couldn't get an answer.");
            thinkingEl.querySelector("p").textContent = data.answer;
            thinkingEl.classList.remove("thinking");
        } catch (err) {
            thinkingEl.remove();
            showError(err.message);
        } finally {
            sending = false;
            chatInput.disabled = false;
            chatSendBtn.disabled = false;
            chatInput.focus();
        }
    }

    chatSendBtn.addEventListener("click", () => sendChat());
    chatInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChat();
        }
    });
    wireSuggestionButtons();

    let clearing = false;
    chatClearBtn.addEventListener("click", async () => {
        if (clearing) return;
        if (!confirm("Clear this document's chat history? This can't be undone.")) return;

        clearing = true;
        chatClearBtn.disabled = true;
        try {
            const res = await fetch(`/api/document/${documentId}/chat`, { method: "DELETE" });
            if (!res.ok) {
                const data = await parseJsonSafe(res).catch(() => ({}));
                throw new Error(data.error || "Couldn't clear the chat.");
            }
            chatLog.innerHTML = CHAT_EMPTY_HTML;
            chatClearBtn.classList.add("hidden");
            wireSuggestionButtons();
            if (window.lucide) lucide.createIcons();
        } catch (err) {
            showError(err.message);
        } finally {
            clearing = false;
            chatClearBtn.disabled = false;
        }
    });
})();
