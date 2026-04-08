document.addEventListener("click", (event) => {
    const toggleButton = event.target.closest("[data-password-toggle]");
    if (!toggleButton) {
        return;
    }

    const explicitSelector = toggleButton.getAttribute("data-password-target");
    let passwordInput = null;

    if (explicitSelector) {
        passwordInput = document.querySelector(explicitSelector);
    } else {
        const group = toggleButton.closest("[data-password-toggle-group]");
        if (group) {
            passwordInput = group.querySelector("[data-password-input]");
        }
    }

    if (!passwordInput) {
        return;
    }

    const showLabel = toggleButton.getAttribute("data-show-label") || "Show";
    const hideLabel = toggleButton.getAttribute("data-hide-label") || "Hide";
    const isHidden = passwordInput.type === "password";

    passwordInput.type = isHidden ? "text" : "password";
    toggleButton.textContent = isHidden ? hideLabel : showLabel;
    toggleButton.setAttribute("aria-pressed", isHidden ? "true" : "false");
});
