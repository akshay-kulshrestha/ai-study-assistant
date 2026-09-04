(() => {
    const errorBox = document.getElementById("error");
    errorBox.setAttribute("aria-live", "assertive");

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.remove("hidden");
        errorBox.scrollIntoView({ behavior: "smooth", block: "center" });
    }

    async function parseJsonSafe(res) {
        try {
            return await res.json();
        } catch {
            throw new Error("Something went wrong. Please try again.");
        }
    }

    // ------------------------------------------------------------------ tabs
    const tabs = [...document.querySelectorAll('[role="tab"]')];

    function activateTab(tab) {
        tabs.forEach(t => {
            const selected = t === tab;
            t.classList.toggle("active", selected);
            t.setAttribute("aria-selected", String(selected));
            t.tabIndex = selected ? 0 : -1;
            document.getElementById(t.getAttribute("aria-controls")).hidden = !selected;
        });
        tab.focus();
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

    // -------------------------------------------------------------- delete
    document.querySelectorAll(".delete-btn").forEach(btn => {
        btn.addEventListener("click", async (e) => {
            e.stopPropagation();
            if (!confirm("Delete this document and all its quizzes/flashcards? This can't be undone.")) return;

            btn.disabled = true;
            try {
                const res = await fetch(`/api/document/${btn.dataset.id}`, { method: "DELETE" });
                if (!res.ok) {
                    const data = await parseJsonSafe(res).catch(() => ({}));
                    throw new Error(data.error || "Could not delete this document.");
                }
                btn.closest(".doc-card-lib").remove();
            } catch (err) {
                btn.disabled = false;
                showError(err.message);
            }
        });
    });
})();
