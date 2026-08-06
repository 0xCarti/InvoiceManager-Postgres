document.addEventListener("DOMContentLoaded", () => {
    const normalizeLabel = (text) => (text || "").replace(/\s+/g, " ").trim();
    const portaledMenus = new WeakMap();
    const portalToggles = new WeakMap();

    const restorePortaledMenu = (toggle) => {
        const state = portaledMenus.get(toggle);
        if (!state) {
            return;
        }

        const { dropdown, menu, placeholder } = state;
        if (placeholder.parentNode) {
            placeholder.replaceWith(menu);
        } else if (dropdown.isConnected) {
            dropdown.appendChild(menu);
        } else {
            menu.remove();
        }
        portalToggles.delete(menu);
        portaledMenus.delete(toggle);
    };

    document.addEventListener("show.bs.dropdown", (event) => {
        const toggle = event.target;
        if (!(toggle instanceof Element)) {
            return;
        }

        const dropdown = toggle.closest(".dropdown[data-dropdown-portal]");
        if (!dropdown || portaledMenus.has(toggle)) {
            return;
        }

        const menu = Array.from(dropdown.children).find((child) => (
            child.classList.contains("dropdown-menu")
        ));
        if (!menu) {
            return;
        }

        const placeholder = document.createComment("dropdown portal");
        menu.replaceWith(placeholder);
        document.body.appendChild(menu);
        portaledMenus.set(toggle, { dropdown, menu, placeholder });
        portalToggles.set(menu, toggle);

        // A later show handler may cancel the dropdown. Restore the menu when
        // Bootstrap therefore never marks the toggle as expanded.
        Promise.resolve().then(() => {
            if (toggle.getAttribute("aria-expanded") !== "true") {
                restorePortaledMenu(toggle);
            }
        });
    }, true);

    document.addEventListener("hidden.bs.dropdown", (event) => {
        const toggle = event.target;
        if (toggle instanceof Element) {
            restorePortaledMenu(toggle);
        }
    }, true);

    // Bootstrap finds a dropdown toggle through the menu's parent during
    // keyboard navigation. Once the menu is under <body>, bridge those keys
    // back to the original toggle and retain the standard focus behavior.
    document.addEventListener("keydown", (event) => {
        const target = event.target;
        if (!(target instanceof Element)) {
            return;
        }

        const menu = target.closest(".dropdown-menu");
        const toggle = menu ? portalToggles.get(menu) : null;
        if (!menu || !toggle) {
            return;
        }

        const isEscape = event.key === "Escape";
        const isArrow = event.key === "ArrowUp" || event.key === "ArrowDown";
        const isTextInput = /input|textarea/i.test(target.tagName);
        if (!isEscape && (!isArrow || isTextInput)) {
            return;
        }

        event.preventDefault();
        event.stopPropagation();

        if (isEscape) {
            bootstrap.Dropdown.getOrCreateInstance(toggle).hide();
            toggle.focus();
            return;
        }

        const items = Array.from(menu.querySelectorAll(
            ".dropdown-item:not(.disabled):not(:disabled)"
        )).filter((item) => (
            item.getClientRects().length > 0 &&
            window.getComputedStyle(item).visibility === "visible"
        ));
        if (!items.length) {
            return;
        }

        const currentItem = target.closest(".dropdown-item");
        const currentIndex = items.indexOf(currentItem);
        let nextIndex;
        if (event.key === "ArrowDown") {
            nextIndex = currentIndex < items.length - 1 ? currentIndex + 1 : 0;
        } else {
            nextIndex = currentIndex > 0 ? currentIndex - 1 : items.length - 1;
        }
        items[nextIndex].focus();
    }, true);

    document.querySelectorAll("table.table-mobile-card").forEach((table) => {
        const headerCells = Array.from(table.querySelectorAll("thead th"));
        const labels = headerCells.map((cell) => normalizeLabel(cell.textContent));

        table.querySelectorAll("tbody tr").forEach((row) => {
            Array.from(row.children).forEach((cell, index) => {
                if (!["TD", "TH"].includes(cell.tagName)) {
                    return;
                }
                if (!cell.dataset.label) {
                    cell.dataset.label = labels[index] || "";
                }
            });
        });
    });
});
