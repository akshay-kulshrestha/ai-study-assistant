(() => {
    const cards = JSON.parse(document.getElementById("flashcards-data").textContent);
    let index = 0;

    const flashcard = document.getElementById("flashcard");
    const questionEl = document.getElementById("fc-question");
    const answerEl = document.getElementById("fc-answer");
    const counterEl = document.getElementById("card-counter");
    const prevBtn = document.getElementById("fc-prev");
    const nextBtn = document.getElementById("fc-next");
    const frontFace = flashcard.querySelector(".flashcard-front");
    const backFace = flashcard.querySelector(".flashcard-back");

    // Keyboard support for the flip card + screen-reader announcements
    flashcard.setAttribute("role", "button");
    flashcard.setAttribute("tabindex", "0");
    flashcard.setAttribute("aria-pressed", "false");
    flashcard.setAttribute("aria-label", "Flashcard, press Enter or Space to flip");
    counterEl.setAttribute("aria-live", "polite");

    function isTypingTarget(el) {
        if (!el) return false;
        const tag = el.tagName;
        return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
    }

    function syncFaceVisibility(flipped) {
        // backface-visibility:hidden only hides the non-visible face
        // visually -- without this, a screen reader still reads both the
        // question and the answer at once, defeating the point of a flip
        // card. Keep only the currently-shown face in the accessibility tree.
        frontFace.setAttribute("aria-hidden", String(flipped));
        backFace.setAttribute("aria-hidden", String(!flipped));
    }

    function flip() {
        const flipped = flashcard.classList.toggle("flipped");
        flashcard.setAttribute("aria-pressed", String(flipped));
        syncFaceVisibility(flipped);
    }

    function renderEmpty() {
        questionEl.textContent = "No flashcards yet";
        answerEl.textContent = "Generate a set of flashcards first, then come back here to study.";
        counterEl.textContent = "0 cards";
        prevBtn.disabled = true;
        nextBtn.disabled = true;
        syncFaceVisibility(false);
    }

    function render() {
        flashcard.classList.remove("flipped");
        flashcard.setAttribute("aria-pressed", "false");
        syncFaceVisibility(false);
        const card = cards[index];
        questionEl.textContent = card.question;
        answerEl.textContent = card.answer;
        counterEl.textContent = `Card ${index + 1} of ${cards.length}`;
        prevBtn.disabled = index === 0;
        nextBtn.disabled = false;
        nextBtn.innerHTML = index === cards.length - 1
            ? 'Restart<i data-lucide="rotate-ccw" class="icon" style="width:16px;height:16px;"></i>'
            : 'Next<i data-lucide="chevron-right" class="icon" style="width:16px;height:16px;"></i>';
        if (window.lucide) lucide.createIcons();
    }

    flashcard.addEventListener("click", flip);
    flashcard.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            flip();
        }
    });

    prevBtn.addEventListener("click", () => {
        if (index > 0) {
            index -= 1;
            render();
        }
    });

    nextBtn.addEventListener("click", () => {
        index = index === cards.length - 1 ? 0 : index + 1;
        render();
    });

    document.addEventListener("keydown", (e) => {
        if (!cards.length || isTypingTarget(document.activeElement)) return;
        if (e.key === "ArrowRight") nextBtn.click();
        if (e.key === "ArrowLeft") prevBtn.click();
    });

    if (!cards.length) {
        renderEmpty();
    } else {
        render();
    }
})();
