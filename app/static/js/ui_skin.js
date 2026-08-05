(function () {
    "use strict";

    const scriptElement = document.currentScript;
    const skinEnabled = scriptElement?.dataset.uiSkinEnabled === "true";
    const storageKey =
        scriptElement?.dataset.uiSkinStorageKey || "assetflow.uiSkin.v1";
    const CLASSIC_SKIN = "classic";
    const DISPATCH_SKIN = "dispatch";
    let activeSkin = CLASSIC_SKIN;

    const normalizeSkin = (value) =>
        value === DISPATCH_SKIN ? DISPATCH_SKIN : CLASSIC_SKIN;

    const readStoredSkin = () => {
        if (!skinEnabled) {
            return CLASSIC_SKIN;
        }
        try {
            return normalizeSkin(window.localStorage.getItem(storageKey));
        } catch (error) {
            return CLASSIC_SKIN;
        }
    };

    const persistSkin = (skin) => {
        if (!skinEnabled) {
            return;
        }
        try {
            window.localStorage.setItem(storageKey, skin);
        } catch (error) {
            // The switch still works for the current page when storage is unavailable.
        }
    };

    const applyRootSkin = (skin) => {
        activeSkin = normalizeSkin(skin);
        if (activeSkin === DISPATCH_SKIN) {
            document.documentElement.dataset.uiSkin = DISPATCH_SKIN;
            return;
        }
        delete document.documentElement.dataset.uiSkin;
    };

    const syncControl = (announce) => {
        const toggle = document.getElementById("uiSkinToggle");
        const isDispatch = activeSkin === DISPATCH_SKIN;
        if (toggle) {
            toggle.setAttribute("aria-checked", isDispatch ? "true" : "false");
            toggle.classList.toggle("is-active", isDispatch);
        }

        if (announce) {
            const status = document.getElementById("uiSkinStatus");
            if (status) {
                status.textContent = isDispatch
                    ? "Dispatch Board interface enabled."
                    : "Current interface restored.";
            }
        }
    };

    const setSkin = (skin, options) => {
        const settings = options || {};
        applyRootSkin(skin);
        if (settings.persist !== false) {
            persistSkin(activeSkin);
        }
        syncControl(Boolean(settings.announce));
        document.dispatchEvent(
            new CustomEvent("assetflow:ui-skin-change", {
                detail: { skin: activeSkin },
            })
        );
    };

    applyRootSkin(readStoredSkin());
    document.documentElement.classList.add("ui-skin-ready");

    document.addEventListener("DOMContentLoaded", () => {
        const toggle = document.getElementById("uiSkinToggle");
        syncControl(false);

        if (toggle) {
            toggle.addEventListener("click", () => {
                const nextSkin =
                    activeSkin === DISPATCH_SKIN ? CLASSIC_SKIN : DISPATCH_SKIN;
                setSkin(nextSkin, { persist: true, announce: true });
            });
        }
    });

    window.addEventListener("storage", (event) => {
        if (!skinEnabled || event.key !== storageKey) {
            return;
        }
        setSkin(normalizeSkin(event.newValue), {
            persist: false,
            announce: false,
        });
    });
})();
