(() => {
    const { quiz_id: quizId } = JSON.parse(document.getElementById("quiz-data").textContent);

    const form = document.getElementById("quiz-form");
    const submitBtn = document.getElementById("submit-quiz-btn");
    const errorBox = document.getElementById("error");
    const resultCard = document.getElementById("quiz-result");

    // Announce validation/submission errors to screen readers as they appear.
    errorBox.setAttribute("aria-live", "assertive");

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

        // The ring is drawn fully empty (offset = c) above, then animated to
        // its real value on the next frame -- setting the final offset in the
        // same paint as the initial one (as before) meant the transition
        // never had a starting state to animate from, so the ring appeared
        // instantly instead of filling in.
        const progressCircle = container.querySelector(".ring-progress");
        requestAnimationFrame(() => {
            requestAnimationFrame(() => {
                progressCircle.style.strokeDashoffset = String(offset);
            });
        });

        return color;
    }

    form.addEventListener("submit", async (e) => {
        e.preventDefault();
        errorBox.classList.add("hidden");

        const questions = [...document.querySelectorAll(".quiz-question")];
        const answers = questions.map((q, i) => {
            const selected = form.querySelector(`input[name="q${i}"]:checked`);
            return selected ? parseInt(selected.value, 10) : null;
        });

        if (answers.some(a => a === null)) {
            errorBox.textContent = "Please answer every question before submitting.";
            errorBox.classList.remove("hidden");
            errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
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

            // A non-2xx response isn't guaranteed to be JSON (a proxy/500 page
            // can return HTML), so don't let a failed res.json() surface a raw
            // "Unexpected token <" parse error to the user.
            let data;
            try {
                data = await res.json();
            } catch {
                throw new Error("Couldn't submit the quiz. Please try again.");
            }

            if (!res.ok) throw new Error(data.error || "Couldn't submit the quiz.");
            renderResults(data.results, data.score, data.total);
        } catch (err) {
            submitBtn.disabled = false;
            submitBtn.innerHTML = '<i data-lucide="check-circle-2" class="icon" style="width:16px;height:16px;"></i>Submit Quiz';
            if (window.lucide) lucide.createIcons();
            errorBox.textContent = err.message;
            errorBox.classList.remove("hidden");
            errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    });

    function renderResults(results, score, total) {
        results.forEach((r, i) => {
            const card = document.querySelector(`.quiz-question[data-index="${i}"]`);
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
            // r.explanation is AI-generated text and must never be inserted as
            // raw HTML (a document could, in principle, cause the model to
            // produce something like <img onerror=...> in its explanation) --
            // innerHTML is only used here for the hardcoded, safe icon markup;
            // the actual dynamic text goes through textContent, same pattern
            // already used correctly elsewhere in this app (see study.js's
            // appendMessage).
            feedback.innerHTML = `<i data-lucide="${r.is_correct ? 'check-circle-2' : 'x-circle'}" class="icon"></i><span></span>`;
            feedback.querySelector("span").textContent =
                `${r.is_correct ? "Correct! " : "Incorrect. "}${r.explanation || ""}`;
        });

        submitBtn.classList.add("hidden");
        resultCard.classList.remove("hidden");
        buildScoreRing(document.getElementById("quiz-score-ring"), score, total, 120, 10);
        const titleEl = document.getElementById("quiz-result-title");
        titleEl.textContent = `${score} / ${total} correct`;
        if (window.lucide) lucide.createIcons();
        resultCard.scrollIntoView({ behavior: "smooth", block: "center" });
        // scrollIntoView only moves the viewport -- without an explicit
        // focus() call, keyboard and screen-reader users aren't actually
        // taken to the result, since focus silently stays on the (now
        // hidden) submit button.
        titleEl.focus();
    }

    document.querySelectorAll(".q-option input").forEach(input => {
        input.addEventListener("change", () => {
            const card = input.closest(".quiz-question");
            card.querySelectorAll(".q-option").forEach(o => o.classList.remove("selected"));
            input.closest(".q-option").classList.add("selected");
        });
    });
})();
