(function () {
    const container = document.querySelector("[data-playlist-items]");
    const addButton = document.getElementById("add-playlist-item");
    const template = document.getElementById("playlist-item-template");

    if (!container || !addButton || !template) {
        return;
    }

    function updateRowState(row) {
        const sourceField = row.querySelector(".playlist-source-type");
        const menuField = row.querySelector(".playlist-menu-field");
        if (!sourceField || !menuField) {
            return;
        }
        const useLocationMenu = sourceField.value === "location_menu";
        menuField.disabled = useLocationMenu;
        if (useLocationMenu) {
            menuField.value = "0";
        }
    }

    function bindRow(row) {
        updateRowState(row);
        const sourceField = row.querySelector(".playlist-source-type");
        const removeButton = row.querySelector("[data-remove-playlist-item]");
        if (sourceField) {
            sourceField.addEventListener("change", function () {
                updateRowState(row);
            });
        }
        if (removeButton) {
            removeButton.addEventListener("click", function () {
                const rows = container.querySelectorAll("[data-playlist-item]");
                if (rows.length <= 1) {
                    row.querySelectorAll("input, select, textarea").forEach(function (field) {
                        if (field.tagName === "SELECT") {
                            field.selectedIndex = 0;
                        } else {
                            field.value = "";
                        }
                    });
                    updateRowState(row);
                    return;
                }
                row.remove();
            });
        }
    }

    addButton.addEventListener("click", function () {
        const nextIndex = parseInt(container.dataset.nextIndex || "0", 10);
        const html = template.innerHTML.replace(/__prefix__/g, String(nextIndex));
        container.insertAdjacentHTML("beforeend", html);
        container.dataset.nextIndex = String(nextIndex + 1);
        const rows = container.querySelectorAll("[data-playlist-item]");
        const newRow = rows[rows.length - 1];
        bindRow(newRow);
    });

    container.querySelectorAll("[data-playlist-item]").forEach(bindRow);
}());
