(() => {
    const dropzone = document.getElementById("dropzone");
    const fileInput = document.getElementById("file-input");
    const uploadingStatus = document.getElementById("uploading-status");
    const errorBox = document.getElementById("error");

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
            throw new Error("Something went wrong. Please try again.");
        }
    }

    dropzone.addEventListener("click", () => fileInput.click());
    dropzone.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            fileInput.click();
        }
    });

    ["dragover", "dragleave", "drop"].forEach(evt => {
        dropzone.addEventListener(evt, (e) => {
            e.preventDefault();
            dropzone.classList.toggle("drag-over", evt === "dragover");
        });
    });

    dropzone.addEventListener("drop", (e) => {
        const file = e.dataTransfer.files[0];
        if (file) uploadFile(file);
    });

    fileInput.addEventListener("change", () => {
        if (fileInput.files[0]) uploadFile(fileInput.files[0]);
    });

    let uploading = false;

    async function uploadFile(file) {
        if (uploading) return;
        uploading = true;

        errorBox.classList.add("hidden");
        dropzone.classList.add("hidden");
        uploadingStatus.classList.remove("hidden");

        const formData = new FormData();
        formData.append("file", file);

        try {
            const res = await fetch("/api/upload", { method: "POST", body: formData });
            const data = await parseJsonSafe(res);
            if (!res.ok) throw new Error(data.error || "Upload failed.");
            window.location.href = `/study/${data.document_id}`;
        } catch (err) {
            uploading = false;
            fileInput.value = "";
            dropzone.classList.remove("hidden");
            uploadingStatus.classList.add("hidden");
            showError(err.message);
        }
    }
})();
