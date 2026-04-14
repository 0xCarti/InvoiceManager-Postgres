(function () {
    const root = document.getElementById("tizen-launcher");
    if (!root) {
        return;
    }

    const storageKey = "signageDisplayToken";
    const activateUrl = root.dataset.activateUrl;
    const shouldReset = root.dataset.reset === "1";
    const form = document.getElementById("launcher-form");
    const activationInput = document.getElementById("activation-code");
    const statusEl = document.getElementById("launcher-status");
    const resetButton = document.getElementById("reset-player");

    function setStatus(message, isError) {
        statusEl.textContent = message || "";
        statusEl.classList.toggle("is-error", Boolean(isError));
    }

    function normaliseCode(value) {
        return String(value || "")
            .toUpperCase()
            .replace(/[^A-Z0-9]/g, "")
            .slice(0, 8);
    }

    function redirectToPlayer(token) {
        if (!token) {
            return;
        }
        window.location.replace("/player/" + encodeURIComponent(token));
    }

    if (shouldReset) {
        window.localStorage.removeItem(storageKey);
    } else {
        const existingToken = window.localStorage.getItem(storageKey);
        if (existingToken) {
            redirectToPlayer(existingToken);
            return;
        }
    }

    activationInput.addEventListener("input", function () {
        activationInput.value = normaliseCode(activationInput.value);
    });

    resetButton.addEventListener("click", function () {
        window.localStorage.removeItem(storageKey);
        activationInput.value = "";
        setStatus("Stored player pairing cleared.", false);
        activationInput.focus();
    });

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        const code = normaliseCode(activationInput.value);
        activationInput.value = code;
        if (!code) {
            setStatus("Enter the activation code shown in Invoice Manager.", true);
            return;
        }

        setStatus("Activating player...", false);
        try {
            const response = await fetch(activateUrl, {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ code: code }),
            });
            const payload = await response.json();
            if (!response.ok || !payload.ok) {
                throw new Error(payload.error || "Activation failed.");
            }
            const token = payload.display && payload.display.public_token;
            if (!token) {
                throw new Error("Activation response did not include a display token.");
            }
            window.localStorage.setItem(storageKey, token);
            setStatus("Activation complete. Loading player...", false);
            redirectToPlayer(token);
        } catch (error) {
            setStatus(error.message || "Activation failed.", true);
        }
    });
}());
