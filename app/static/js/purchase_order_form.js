(function () {
    "use strict";

    function toNumber(value) {
        const parsed = Number(value);
        return Number.isFinite(parsed) ? parsed : 0;
    }

    function initPurchaseOrderForm(config) {
        const container = config && config.container ? config.container : null;
        if (!container) {
            return;
        }

        const addRowButton = config.addRowButton || null;
        const quickAddButton = config.quickAddButton || null;
        const saveNewItemButton = config.saveNewItemButton || null;
        const newItemModalEl = config.newItemModal || null;
        const manageUnitsModalEl =
            (config && config.manageUnitsModal) ||
            document.getElementById("manageUnitsModal");
        const searchTimers = new WeakMap();
        const defaultGlCodeLabel = "Unassigned";

        function formatGlCode(value) {
            if (typeof value === "string") {
                const trimmed = value.trim();
                if (trimmed) {
                    return trimmed;
                }
            }
            return defaultGlCodeLabel;
        }

        function updateRowGlCode(row, glCode) {
            if (!row) {
                return;
            }
            const display = row.querySelector(".gl-code-display");
            if (!display) {
                return;
            }
            display.textContent = formatGlCode(glCode || "");
            display.dataset.glCode = glCode || "";
        }

        function setManageButtonState(row, enabled) {
            if (!row) {
                return;
            }
            const button = row.querySelector(".manage-units-button");
            if (button) {
                button.disabled = !enabled;
            }
        }

        let nextIndex = toNumber(
            config.nextIndex !== undefined
                ? config.nextIndex
                : container.dataset.nextIndex
        );
        if (nextIndex < container.querySelectorAll(".item-row").length) {
            nextIndex = container.querySelectorAll(".item-row").length;
        }
        container.dataset.nextIndex = String(nextIndex);

        let newItemModal = null;
        if (newItemModalEl && typeof bootstrap !== "undefined") {
            newItemModal =
                bootstrap.Modal.getInstance(newItemModalEl) ||
                new bootstrap.Modal(newItemModalEl);
        }

        let manageUnitsModal = null;
        if (manageUnitsModalEl && typeof bootstrap !== "undefined") {
            manageUnitsModal =
                bootstrap.Modal.getInstance(manageUnitsModalEl) ||
                new bootstrap.Modal(manageUnitsModalEl);
        }

        const baseUnitSelect = document.getElementById("new-item-base-unit");
        const newItemUnitsContainer = document.getElementById("new-item-units");
        const addNewItemUnitButton = document.getElementById("add-new-item-unit");
        const manageUnitsContainer = document.getElementById("manage-units-rows");
        const addManageUnitButton = document.getElementById("add-manage-unit");
        const saveManageUnitsButton = document.getElementById("save-manage-units");
        const manageUnitsItemNameInput = document.getElementById(
            "manage-units-item-name"
        );
        const manageUnitsBaseUnitInput = document.getElementById(
            "manage-units-base-unit"
        );
        const manageUnitsAlert = document.getElementById("manage-units-alert");
        let newItemUnitIndex = 0;
        let manageUnitsIndex = 0;
        let manageUnitsCurrentItemId = null;
        let manageUnitsTargetRow = null;
        let preparedDragRow = null;
        let activeDragRow = null;
        let skipNextPointerCancel = false;
        let pointerCancelFallbackTimer = null;
        const supportsPointerEvents =
            typeof window !== "undefined" &&
            typeof window.PointerEvent !== "undefined";

        function syncBaseUnitRow() {
            if (!newItemUnitsContainer) {
                return;
            }
            const baseRow = newItemUnitsContainer.querySelector(
                '.new-item-unit-row[data-base="true"]'
            );
            if (!baseRow) {
                return;
            }
            const nameInput = baseRow.querySelector(".new-item-unit-name");
            if (nameInput && baseUnitSelect) {
                nameInput.value = baseUnitSelect.value || "";
            }
        }

        function ensureUnitDefaults() {
            if (!newItemUnitsContainer) {
                return;
            }
            const receivingChecked = newItemUnitsContainer.querySelector(
                ".new-item-unit-receiving:checked"
            );
            if (!receivingChecked) {
                const fallback = newItemUnitsContainer.querySelector(
                    '.new-item-unit-row[data-base="true"] .new-item-unit-receiving'
                );
                if (fallback) {
                    fallback.checked = true;
                }
            }

            const transferChecked = newItemUnitsContainer.querySelector(
                ".new-item-unit-transfer:checked"
            );
            if (!transferChecked) {
                const fallback = newItemUnitsContainer.querySelector(
                    '.new-item-unit-row[data-base="true"] .new-item-unit-transfer'
                );
                if (fallback) {
                    fallback.checked = true;
                }
            }
        }

        function showManageUnitsError(message) {
            if (!manageUnitsAlert) {
                return;
            }
            if (message) {
                manageUnitsAlert.textContent = message;
                manageUnitsAlert.classList.remove("d-none");
            } else {
                manageUnitsAlert.textContent = "";
                manageUnitsAlert.classList.add("d-none");
            }
        }

        function resetManageUnitsRows() {
            if (!manageUnitsContainer) {
                return;
            }
            manageUnitsContainer.innerHTML = "";
            manageUnitsIndex = 0;
        }

        function ensureManageUnitDefaults() {
            if (!manageUnitsContainer) {
                return;
            }
            const receivingChecked = manageUnitsContainer.querySelector(
                ".manage-unit-receiving:checked"
            );
            if (!receivingChecked) {
                const fallback = manageUnitsContainer.querySelector(
                    '.manage-unit-row[data-base="true"] .manage-unit-receiving'
                );
                if (fallback) {
                    fallback.checked = true;
                }
            }

            const transferChecked = manageUnitsContainer.querySelector(
                ".manage-unit-transfer:checked"
            );
            if (!transferChecked) {
                const fallback = manageUnitsContainer.querySelector(
                    '.manage-unit-row[data-base="true"] .manage-unit-transfer'
                );
                if (fallback) {
                    fallback.checked = true;
                }
            }
        }

        function createManageUnitRow(options = {}) {
            if (!manageUnitsContainer) {
                return null;
            }
            const index = manageUnitsIndex++;
            const isBase = Boolean(options.isBase);
            const row = document.createElement("div");
            row.classList.add(
                "row",
                "g-2",
                "align-items-end",
                "manage-unit-row"
            );
            row.dataset.base = isBase ? "true" : "false";
            row.dataset.unitId = options.id ? String(options.id) : "";

            const nameCol = document.createElement("div");
            nameCol.classList.add("col-12", "col-md-4");
            const nameLabel = document.createElement("label");
            nameLabel.classList.add("form-label");
            const nameId = `manage-unit-${index}-name`;
            nameLabel.setAttribute("for", nameId);
            nameLabel.textContent = isBase ? "Base Unit" : "Unit Name";
            const nameInput = document.createElement("input");
            nameInput.type = "text";
            nameInput.classList.add("form-control", "manage-unit-name");
            nameInput.id = nameId;
            nameInput.value = options.name || "";
            if (isBase) {
                nameInput.readOnly = true;
            } else {
                nameInput.placeholder = "e.g. Case";
            }
            nameCol.append(nameLabel, nameInput);
            row.appendChild(nameCol);

            const factorCol = document.createElement("div");
            factorCol.classList.add("col-12", "col-md-3");
            const factorLabel = document.createElement("label");
            factorLabel.classList.add("form-label");
            const factorId = `manage-unit-${index}-factor`;
            factorLabel.setAttribute("for", factorId);
            factorLabel.textContent = "Ratio to Base Unit";
            const factorInput = document.createElement("input");
            factorInput.type = "number";
            factorInput.classList.add("form-control", "manage-unit-factor");
            factorInput.id = factorId;
            factorInput.step = "any";
            factorInput.min = "0";
            factorInput.value =
                options.factor !== undefined && options.factor !== null
                    ? options.factor
                    : 1;
            if (isBase) {
                factorInput.readOnly = true;
                factorInput.value = 1;
            }
            factorCol.append(factorLabel, factorInput);
            row.appendChild(factorCol);

            const receivingCol = document.createElement("div");
            receivingCol.classList.add(
                "col-6",
                "col-md-2",
                "d-flex",
                "align-items-center"
            );
            const receivingWrapper = document.createElement("div");
            receivingWrapper.classList.add("form-check");
            const receivingId = `manage-unit-${index}-receiving`;
            const receivingInput = document.createElement("input");
            receivingInput.type = "radio";
            receivingInput.classList.add(
                "form-check-input",
                "manage-unit-receiving"
            );
            receivingInput.name = "manage-unit-receiving";
            receivingInput.id = receivingId;
            receivingInput.checked = Boolean(options.receivingDefault);
            const receivingLabel = document.createElement("label");
            receivingLabel.classList.add("form-check-label");
            receivingLabel.setAttribute("for", receivingId);
            receivingLabel.textContent = "Receiving Default";
            receivingWrapper.append(receivingInput, receivingLabel);
            receivingCol.appendChild(receivingWrapper);
            row.appendChild(receivingCol);

            const transferCol = document.createElement("div");
            transferCol.classList.add(
                "col-6",
                "col-md-2",
                "d-flex",
                "align-items-center"
            );
            const transferWrapper = document.createElement("div");
            transferWrapper.classList.add("form-check");
            const transferId = `manage-unit-${index}-transfer`;
            const transferInput = document.createElement("input");
            transferInput.type = "radio";
            transferInput.classList.add(
                "form-check-input",
                "manage-unit-transfer"
            );
            transferInput.name = "manage-unit-transfer";
            transferInput.id = transferId;
            transferInput.checked = Boolean(options.transferDefault);
            const transferLabel = document.createElement("label");
            transferLabel.classList.add("form-check-label");
            transferLabel.setAttribute("for", transferId);
            transferLabel.textContent = "Transfer Default";
            transferWrapper.append(transferInput, transferLabel);
            transferCol.appendChild(transferWrapper);
            row.appendChild(transferCol);

            if (!isBase) {
                const removeCol = document.createElement("div");
                removeCol.classList.add(
                    "col-12",
                    "col-md-1",
                    "d-flex",
                    "justify-content-md-end"
                );
                const removeButton = document.createElement("button");
                removeButton.type = "button";
                removeButton.classList.add(
                    "btn",
                    "btn-outline-danger",
                    "btn-sm",
                    "manage-unit-remove"
                );
                removeButton.textContent = "Delete";
                removeCol.appendChild(removeButton);
                row.appendChild(removeCol);
            } else {
                const spacerCol = document.createElement("div");
                spacerCol.classList.add("col-12", "col-md-1");
                row.appendChild(spacerCol);
            }

            return row;
        }

        function appendManageUnitRow(options = {}) {
            if (!manageUnitsContainer) {
                return null;
            }
            const row = createManageUnitRow(options);
            if (!row) {
                return null;
            }
            manageUnitsContainer.appendChild(row);
            return row;
        }

        function loadManageUnits(itemId, itemName) {
            if (!manageUnitsContainer) {
                return;
            }
            manageUnitsCurrentItemId = itemId;
            manageUnitsIndex = 0;
            showManageUnitsError("");
            if (manageUnitsItemNameInput) {
                manageUnitsItemNameInput.value = itemName || "";
            }
            if (manageUnitsBaseUnitInput) {
                manageUnitsBaseUnitInput.value = "";
            }
            manageUnitsContainer.innerHTML =
                '<div class="text-muted">Loading...</div>';

            fetch(`/items/${itemId}/units`)
                .then((response) => {
                    if (!response.ok) {
                        throw new Error("Failed to load units");
                    }
                    return response.json();
                })
                .then((data) => {
                    if (manageUnitsBaseUnitInput) {
                        manageUnitsBaseUnitInput.value = data.base_unit || "";
                    }
                    resetManageUnitsRows();
                    const units = Array.isArray(data.units) ? data.units : [];
                    const baseUnitName = data.base_unit || "";
                    const baseUnitEntry = units.find(
                        (unit) => unit.name === baseUnitName
                    );
                    appendManageUnitRow({
                        id: baseUnitEntry ? baseUnitEntry.id : null,
                        name: baseUnitName,
                        factor: 1,
                        receivingDefault: baseUnitEntry
                            ? Boolean(baseUnitEntry.receiving_default)
                            : true,
                        transferDefault: baseUnitEntry
                            ? Boolean(baseUnitEntry.transfer_default)
                            : true,
                        isBase: true,
                    });
                    const remainingUnits = units.filter((unit) => {
                        if (baseUnitEntry && unit.id === baseUnitEntry.id) {
                            return false;
                        }
                        return unit.name !== baseUnitName;
                    });
                    remainingUnits.forEach((unit) => {
                        appendManageUnitRow({
                            id: unit.id,
                            name: unit.name,
                            factor: unit.factor,
                            receivingDefault: Boolean(
                                unit.receiving_default
                            ),
                            transferDefault: Boolean(
                                unit.transfer_default
                            ),
                            isBase: false,
                        });
                    });
                    ensureManageUnitDefaults();
                })
                .catch(() => {
                    resetManageUnitsRows();
                    showManageUnitsError(
                        "Unable to load units for this item."
                    );
                });
        }

        function openManageUnitsForRow(row) {
            if (!row) {
                return;
            }
            const hiddenField = row.querySelector(".item-id-field");
            if (!hiddenField || !hiddenField.value) {
                return;
            }
            manageUnitsTargetRow = row;
            const nameField = row.querySelector(".item-search");
            const itemName = nameField ? nameField.value.trim() : "";
            if (manageUnitsModal) {
                manageUnitsModal.show();
            }
            loadManageUnits(hiddenField.value, itemName);
        }

        function createNewItemUnitRow(options = {}) {
            const index = newItemUnitIndex++;
            const isBase = Boolean(options.isBase);
            const row = document.createElement("div");
            row.classList.add(
                "row",
                "g-2",
                "align-items-end",
                "new-item-unit-row"
            );
            row.dataset.base = isBase ? "true" : "false";

            const nameCol = document.createElement("div");
            nameCol.classList.add("col-12", "col-md-4");
            const nameLabel = document.createElement("label");
            nameLabel.classList.add("form-label");
            const nameId = `new-item-unit-${index}-name`;
            nameLabel.setAttribute("for", nameId);
            nameLabel.textContent = isBase ? "Base Unit" : "Unit Name";
            const nameInput = document.createElement("input");
            nameInput.type = "text";
            nameInput.classList.add("form-control", "new-item-unit-name");
            nameInput.id = nameId;
            nameInput.value = options.name || "";
            if (isBase) {
                nameInput.readOnly = true;
            } else {
                nameInput.placeholder = "e.g. Case";
            }
            nameCol.append(nameLabel, nameInput);
            row.appendChild(nameCol);

            const factorCol = document.createElement("div");
            factorCol.classList.add("col-12", "col-md-3");
            const factorLabel = document.createElement("label");
            factorLabel.classList.add("form-label");
            const factorId = `new-item-unit-${index}-factor`;
            factorLabel.setAttribute("for", factorId);
            factorLabel.textContent = "Ratio to Base Unit";
            const factorInput = document.createElement("input");
            factorInput.type = "number";
            factorInput.classList.add("form-control", "new-item-unit-factor");
            factorInput.id = factorId;
            factorInput.step = "any";
            factorInput.min = "0";
            factorInput.value =
                options.factor !== undefined && options.factor !== null
                    ? options.factor
                    : 1;
            if (isBase) {
                factorInput.readOnly = true;
                factorInput.value = 1;
            }
            factorCol.append(factorLabel, factorInput);
            row.appendChild(factorCol);

            const receivingCol = document.createElement("div");
            receivingCol.classList.add(
                "col-6",
                "col-md-2",
                "d-flex",
                "align-items-center"
            );
            const receivingWrapper = document.createElement("div");
            receivingWrapper.classList.add("form-check");
            const receivingId = `new-item-unit-${index}-receiving`;
            const receivingInput = document.createElement("input");
            receivingInput.type = "radio";
            receivingInput.classList.add(
                "form-check-input",
                "new-item-unit-receiving"
            );
            receivingInput.name = "new-item-receiving-default";
            receivingInput.id = receivingId;
            if (options.receivingDefault) {
                receivingInput.checked = true;
            }
            const receivingLabel = document.createElement("label");
            receivingLabel.classList.add("form-check-label");
            receivingLabel.setAttribute("for", receivingId);
            receivingLabel.textContent = "Receiving Default";
            receivingWrapper.append(receivingInput, receivingLabel);
            receivingCol.appendChild(receivingWrapper);
            row.appendChild(receivingCol);

            const transferCol = document.createElement("div");
            transferCol.classList.add(
                "col-6",
                "col-md-2",
                "d-flex",
                "align-items-center"
            );
            const transferWrapper = document.createElement("div");
            transferWrapper.classList.add("form-check");
            const transferId = `new-item-unit-${index}-transfer`;
            const transferInput = document.createElement("input");
            transferInput.type = "radio";
            transferInput.classList.add(
                "form-check-input",
                "new-item-unit-transfer"
            );
            transferInput.name = "new-item-transfer-default";
            transferInput.id = transferId;
            if (options.transferDefault) {
                transferInput.checked = true;
            }
            const transferLabel = document.createElement("label");
            transferLabel.classList.add("form-check-label");
            transferLabel.setAttribute("for", transferId);
            transferLabel.textContent = "Transfer Default";
            transferWrapper.append(transferInput, transferLabel);
            transferCol.appendChild(transferWrapper);
            row.appendChild(transferCol);

            if (!isBase) {
                const removeCol = document.createElement("div");
                removeCol.classList.add(
                    "col-12",
                    "col-md-1",
                    "d-flex",
                    "align-items-center"
                );
                const removeButton = document.createElement("button");
                removeButton.type = "button";
                removeButton.classList.add(
                    "btn",
                    "btn-outline-danger",
                    "btn-sm",
                    "w-100",
                    "new-item-unit-remove"
                );
                removeButton.textContent = "Remove";
                removeButton.setAttribute("aria-label", "Remove unit");
                removeCol.appendChild(removeButton);
                row.appendChild(removeCol);
            }

            return row;
        }

        function appendNewItemUnitRow(options = {}) {
            if (!newItemUnitsContainer) {
                return null;
            }
            const row = createNewItemUnitRow(options);
            newItemUnitsContainer.appendChild(row);
            ensureUnitDefaults();
            return row;
        }

        function resetNewItemUnitRows() {
            if (!newItemUnitsContainer) {
                return;
            }
            newItemUnitsContainer.innerHTML = "";
            newItemUnitIndex = 0;
            const baseName = baseUnitSelect ? baseUnitSelect.value || "" : "";
            appendNewItemUnitRow({
                name: baseName,
                factor: 1,
                receivingDefault: true,
                transferDefault: true,
                isBase: true,
            });
            syncBaseUnitRow();
            ensureUnitDefaults();
        }

        resetNewItemUnitRows();

        function closeAllSuggestionLists(exceptList) {
            container.querySelectorAll(".suggestion-list").forEach((list) => {
                if (list !== exceptList) {
                    list.classList.add("d-none");
                }
            });
        }

        function clearSuggestions(list) {
            if (list) {
                list.innerHTML = "";
                list.classList.add("d-none");
            }
        }

        function clearUnits(unitSelect) {
            if (unitSelect) {
                unitSelect.innerHTML = "";
                unitSelect.dataset.selected = "";
            }
        }

        function fetchUnits(itemId, unitSelect, selectedUnitId) {
            if (!unitSelect || !itemId) {
                clearUnits(unitSelect);
                return;
            }

            fetch(`/items/${itemId}/units`)
                .then((response) => {
                    if (!response.ok) {
                        throw new Error("Failed to load units");
                    }
                    return response.json();
                })
                .then((data) => {
                    const options = data.units
                        .map((unit) => {
                            const shouldSelect = selectedUnitId
                                ? parseInt(selectedUnitId, 10) === unit.id
                                : unit.receiving_default;
                            const plural = unit.factor !== 1 ? "s" : "";
                            return `
                                <option value="${unit.id}" ${
                                shouldSelect ? "selected" : ""
                            }>
                                    ${unit.name} of ${unit.factor} ${data.base_unit}${plural}
                                </option>
                            `;
                        })
                        .join("");
                    unitSelect.innerHTML = options;
                    unitSelect.dataset.selected = "";
                })
                .catch(() => {
                    clearUnits(unitSelect);
                });
        }

        function createRowElement(index, options = {}) {
            const row = document.createElement("div");
            row.classList.add("row", "g-2", "mt-2", "item-row", "align-items-center");

            const itemCol = document.createElement("div");
            itemCol.classList.add("col", "position-relative");

            const searchInput = document.createElement("input");
            searchInput.type = "text";
            searchInput.name = `items-${index}-item-label`;
            searchInput.classList.add("form-control", "item-search");
            searchInput.placeholder = "Search for an item";
            searchInput.autocomplete = "off";
            if (options.itemName) {
                searchInput.value = options.itemName;
            }
            itemCol.appendChild(searchInput);

            const hiddenInput = document.createElement("input");
            hiddenInput.type = "hidden";
            hiddenInput.name = `items-${index}-item`;
            hiddenInput.classList.add("item-id-field");
            if (options.itemId) {
                hiddenInput.value = options.itemId;
            }
            itemCol.appendChild(hiddenInput);

            const positionInput = document.createElement("input");
            positionInput.type = "hidden";
            positionInput.name = `items-${index}-position`;
            positionInput.classList.add("item-position");
            if (options.position !== undefined && options.position !== null) {
                positionInput.value = options.position;
            }
            itemCol.appendChild(positionInput);

            const suggestionList = document.createElement("div");
            suggestionList.classList.add(
                "list-group",
                "suggestion-list",
                "d-none",
                "position-absolute",
                "w-100"
            );
            suggestionList.style.zIndex = "1000";
            suggestionList.style.maxHeight = "200px";
            suggestionList.style.overflowY = "auto";
            itemCol.appendChild(suggestionList);

            const glCodeCol = document.createElement("div");
            glCodeCol.classList.add("col-auto", "gl-code-column");
            const glCodeBadge = document.createElement("span");
            glCodeBadge.classList.add(
                "gl-code-display",
                "badge",
                "bg-light",
                "text-dark"
            );
            glCodeBadge.textContent = formatGlCode(options.glCode || "");
            glCodeBadge.dataset.glCode = options.glCode || "";
            glCodeCol.appendChild(glCodeBadge);

            const unitCol = document.createElement("div");
            unitCol.classList.add("col");
            const unitWrapper = document.createElement("div");
            unitWrapper.classList.add("d-flex", "flex-column", "gap-1");
            const unitSelect = document.createElement("select");
            unitSelect.name = `items-${index}-unit`;
            unitSelect.classList.add("form-control", "unit-select");
            unitSelect.dataset.selected = options.unitId
                ? String(options.unitId)
                : "";
            unitWrapper.appendChild(unitSelect);
            const manageButton = document.createElement("button");
            manageButton.type = "button";
            manageButton.classList.add(
                "btn",
                "btn-link",
                "btn-sm",
                "p-0",
                "text-start",
                "manage-units-button"
            );
            manageButton.textContent = "Edit units";
            manageButton.setAttribute(
                "aria-label",
                "Edit units of measure"
            );
            manageButton.disabled = true;
            unitWrapper.appendChild(manageButton);
            unitCol.appendChild(unitWrapper);

            const quantityCol = document.createElement("div");
            quantityCol.classList.add("col");
            const quantityInput = document.createElement("input");
            quantityInput.type = "number";
            quantityInput.step = "any";
            quantityInput.name = `items-${index}-quantity`;
            quantityInput.classList.add("form-control", "quantity");
            if (options.quantity !== undefined && options.quantity !== null) {
                quantityInput.value = options.quantity;
            }
            quantityCol.appendChild(quantityInput);

            const reorderCol = document.createElement("div");
            reorderCol.classList.add("col-auto");
            const dragButton = document.createElement("button");
            dragButton.type = "button";
            dragButton.classList.add(
                "btn",
                "btn-outline-secondary",
                "btn-sm",
                "drag-handle"
            );
            dragButton.setAttribute("aria-label", "Drag to reorder");
            dragButton.setAttribute("title", "Drag to reorder");
            dragButton.textContent = "=";
            reorderCol.appendChild(dragButton);

            const removeCol = document.createElement("div");
            removeCol.classList.add("col-auto");
            const removeButton = document.createElement("button");
            removeButton.type = "button";
            removeButton.classList.add("btn", "btn-danger", "remove-item");
            removeButton.textContent = "Remove";
            removeCol.appendChild(removeButton);

            row.append(
                itemCol,
                glCodeCol,
                unitCol,
                quantityCol,
                reorderCol,
                removeCol
            );
            return row;
        }

        function addRow(options = {}) {
            const row = createRowElement(nextIndex, options);
            container.appendChild(row);
            nextIndex += 1;
            container.dataset.nextIndex = String(nextIndex);

            updatePositions();

            updateRowGlCode(row, options.glCode || "");
            setManageButtonState(row, Boolean(options.itemId));

            const unitSelect = row.querySelector(".unit-select");
            if (options.itemId) {
                fetchUnits(options.itemId, unitSelect, options.unitId || null);
            }

            if (!options.itemId) {
                const searchInput = row.querySelector(".item-search");
                if (searchInput) {
                    searchInput.focus();
                }
            }

            return row;
        }

        function updatePositions() {
            const rows = Array.from(container.querySelectorAll(".item-row"));
            rows.forEach((row, index) => {
                const positionField = row.querySelector(".item-position");
                if (positionField) {
                    positionField.value = String(index);
                }
            });
        }

        function clearPreparedDragRow() {
            if (!preparedDragRow) {
                return;
            }
            preparedDragRow.removeAttribute("draggable");
            delete preparedDragRow.dataset.dragPrepared;
            preparedDragRow = null;
        }

        function prepareRowForDrag(row) {
            if (!row) {
                return;
            }
            if (preparedDragRow && preparedDragRow !== row && !activeDragRow) {
                clearPreparedDragRow();
            }
            preparedDragRow = row;
            row.dataset.dragPrepared = "true";
            row.setAttribute("draggable", "true");
        }

        function handleDragPrepare(event) {
            const handle = event.target.closest(".drag-handle");
            if (!handle) {
                return;
            }
            const row = handle.closest(".item-row");
            if (!row) {
                return;
            }
            prepareRowForDrag(row);

            if (supportsPointerEvents && event.type === "pointerdown") {
                skipNextPointerCancel = true;
                if (pointerCancelFallbackTimer) {
                    clearTimeout(pointerCancelFallbackTimer);
                    pointerCancelFallbackTimer = null;
                }
            } else if (!supportsPointerEvents && event.type === "touchstart") {
                skipNextPointerCancel = true;
                if (pointerCancelFallbackTimer) {
                    clearTimeout(pointerCancelFallbackTimer);
                    pointerCancelFallbackTimer = null;
                }
            }
        }

        function handlePointerLikeCleanup(event) {
            if (event) {
                const eventType = event.type;

                if (
                    (eventType === "pointercancel" || eventType === "touchcancel") &&
                    skipNextPointerCancel
                ) {
                    skipNextPointerCancel = false;
                    if (pointerCancelFallbackTimer) {
                        clearTimeout(pointerCancelFallbackTimer);
                    }
                    pointerCancelFallbackTimer = setTimeout(() => {
                        pointerCancelFallbackTimer = null;
                        if (!activeDragRow) {
                            clearPreparedDragRow();
                        }
                    }, 0);
                    return;
                }

                if (
                    eventType === "pointerup" ||
                    eventType === "mouseup" ||
                    eventType === "touchend"
                ) {
                    skipNextPointerCancel = false;
                    if (pointerCancelFallbackTimer) {
                        clearTimeout(pointerCancelFallbackTimer);
                        pointerCancelFallbackTimer = null;
                    }
                }
            }

            if (activeDragRow) {
                return;
            }
            clearPreparedDragRow();
        }

        function performSearch(input, term) {
            const row = input.closest(".item-row");
            if (!row) {
                return;
            }
            const suggestionList = row.querySelector(".suggestion-list");
            if (!suggestionList) {
                return;
            }

            fetch(`/items/search?term=${encodeURIComponent(term)}`)
                .then((response) => {
                    if (!response.ok) {
                        throw new Error("Search failed");
                    }
                    return response.json();
                })
                .then((items) => {
                    if (input.value.trim() !== term) {
                        return;
                    }

                    suggestionList.innerHTML = "";
                    if (!items.length) {
                        suggestionList.classList.add("d-none");
                        return;
                    }

                    items.forEach((item) => {
                        const option = document.createElement("button");
                        option.type = "button";
                        option.className =
                            "list-group-item list-group-item-action suggestion-option";
                        option.textContent = item.name;
                        option.dataset.itemId = item.id;
                        option.dataset.itemName = item.name;
                        option.dataset.glCode = item.gl_code || "";
                        suggestionList.appendChild(option);
                    });

                    suggestionList.classList.remove("d-none");
                })
                .catch(() => {
                    suggestionList.classList.add("d-none");
                });
        }

        function handleSearchInput(input) {
            const row = input.closest(".item-row");
            if (!row) {
                return;
            }
            const hiddenField = row.querySelector(".item-id-field");
            const unitSelect = row.querySelector(".unit-select");
            const suggestionList = row.querySelector(".suggestion-list");

            if (hiddenField) {
                hiddenField.value = "";
            }
            setManageButtonState(row, false);
            updateRowGlCode(row, "");
            clearUnits(unitSelect);
            closeAllSuggestionLists(suggestionList);

            const term = input.value.trim();
            if (!term) {
                clearSuggestions(suggestionList);
                return;
            }

            if (searchTimers.has(input)) {
                clearTimeout(searchTimers.get(input));
            }
            const timer = setTimeout(() => {
                performSearch(input, term);
            }, 150);
            searchTimers.set(input, timer);
        }

        function handleSuggestionSelection(option) {
            const row = option.closest(".item-row");
            if (!row) {
                return;
            }

            const hiddenField = row.querySelector(".item-id-field");
            const searchInput = row.querySelector(".item-search");
            const unitSelect = row.querySelector(".unit-select");
            const suggestionList = row.querySelector(".suggestion-list");

            if (hiddenField) {
                hiddenField.value = option.dataset.itemId || "";
            }
            if (searchInput) {
                searchInput.value = option.dataset.itemName || "";
            }
            updateRowGlCode(row, option.dataset.glCode || "");
            setManageButtonState(row, true);
            clearSuggestions(suggestionList);
            fetchUnits(option.dataset.itemId, unitSelect);

            const quantityInput = row.querySelector(".quantity");
            if (quantityInput) {
                quantityInput.focus();
            }
        }

        function handleSearchKeydown(event) {
            const input = event.target;
            const row = input.closest(".item-row");
            if (!row) {
                return;
            }
            const suggestionList = row.querySelector(".suggestion-list");
            if (!suggestionList) {
                return;
            }

            if (event.key === "Enter") {
                const firstOption = suggestionList.querySelector(".suggestion-option");
                if (!suggestionList.classList.contains("d-none") && firstOption) {
                    event.preventDefault();
                    handleSuggestionSelection(firstOption);
                }
            } else if (event.key === "Escape") {
                suggestionList.classList.add("d-none");
            }
        }

        function handleQuantityKeydown(event) {
            if (event.key !== "Tab" || event.shiftKey) {
                return;
            }
            const currentRow = event.target.closest(".item-row");
            if (!currentRow) {
                return;
            }
            const nextRow = currentRow.nextElementSibling;
            if (!nextRow) {
                return;
            }
            const nextQuantity = nextRow.querySelector(".quantity");
            if (nextQuantity) {
                event.preventDefault();
                nextQuantity.focus();
            }
        }

        if (addRowButton) {
            addRowButton.addEventListener("click", (event) => {
                event.preventDefault();
                addRow();
            });
        }

        if (baseUnitSelect) {
            baseUnitSelect.addEventListener("change", () => {
                syncBaseUnitRow();
                ensureUnitDefaults();
            });
        }

        if (addNewItemUnitButton) {
            addNewItemUnitButton.addEventListener("click", () => {
                const row = appendNewItemUnitRow();
                if (!row) {
                    return;
                }
                const nameInput = row.querySelector(".new-item-unit-name");
                if (nameInput && !nameInput.readOnly) {
                    nameInput.focus();
                }
            });
        }

        if (addManageUnitButton) {
            addManageUnitButton.addEventListener("click", () => {
                const row = appendManageUnitRow({
                    name: "",
                    factor: 1,
                    receivingDefault: false,
                    transferDefault: false,
                });
                if (!row) {
                    return;
                }
                const nameInput = row.querySelector(".manage-unit-name");
                if (nameInput && !nameInput.readOnly) {
                    nameInput.focus();
                }
                ensureManageUnitDefaults();
            });
        }

        if (manageUnitsContainer) {
            manageUnitsContainer.addEventListener("click", (event) => {
                const target = event.target;
                if (!(target instanceof Element)) {
                    return;
                }
                const removeButton = target.closest(".manage-unit-remove");
                if (!removeButton) {
                    return;
                }
                const row = removeButton.closest(".manage-unit-row");
                if (row && row.dataset.base !== "true") {
                    row.remove();
                    ensureManageUnitDefaults();
                }
            });

            manageUnitsContainer.addEventListener("change", (event) => {
                const target = event.target;
                if (!(target instanceof Element)) {
                    return;
                }
                if (
                    target.classList.contains("manage-unit-receiving") ||
                    target.classList.contains("manage-unit-transfer")
                ) {
                    ensureManageUnitDefaults();
                }
            });
        }

        if (manageUnitsModalEl) {
            manageUnitsModalEl.addEventListener("hidden.bs.modal", () => {
                manageUnitsCurrentItemId = null;
                manageUnitsTargetRow = null;
                resetManageUnitsRows();
                showManageUnitsError("");
                if (manageUnitsItemNameInput) {
                    manageUnitsItemNameInput.value = "";
                }
                if (manageUnitsBaseUnitInput) {
                    manageUnitsBaseUnitInput.value = "";
                }
            });
        }

        if (saveManageUnitsButton) {
            saveManageUnitsButton.addEventListener("click", () => {
                if (!manageUnitsContainer || !manageUnitsCurrentItemId) {
                    return;
                }
                showManageUnitsError("");
                const itemId = manageUnitsCurrentItemId;
                const unitRows = Array.from(
                    manageUnitsContainer.querySelectorAll(".manage-unit-row")
                );
                if (!unitRows.length) {
                    showManageUnitsError(
                        "Add at least one unit before saving."
                    );
                    return;
                }
                const baseUnit = manageUnitsBaseUnitInput
                    ? manageUnitsBaseUnitInput.value.trim()
                    : "";
                const unitsPayload = [];
                let hasInvalid = false;
                let receivingAssigned = false;
                let transferAssigned = false;

                unitRows.forEach((row) => {
                    const isBase = row.dataset.base === "true";
                    const unitIdValue = row.dataset.unitId || "";
                    let unitId = null;
                    if (unitIdValue) {
                        const parsed = parseInt(unitIdValue, 10);
                        if (!Number.isNaN(parsed)) {
                            unitId = parsed;
                        }
                    }
                    const nameField = row.querySelector(".manage-unit-name");
                    const factorField = row.querySelector(
                        ".manage-unit-factor"
                    );
                    let unitName = nameField ? nameField.value.trim() : "";
                    if (isBase && baseUnit) {
                        unitName = baseUnit;
                    }
                    let factorValue = factorField
                        ? window.NumericInput
                            ? window.NumericInput.parseValue(factorField)
                            : parseFloat(factorField.value)
                        : NaN;
                    if (isBase) {
                        factorValue = 1;
                    }
                    const receivingField = row.querySelector(
                        ".manage-unit-receiving"
                    );
                    const transferField = row.querySelector(
                        ".manage-unit-transfer"
                    );
                    let receivingDefault = receivingField
                        ? receivingField.checked
                        : false;
                    let transferDefault = transferField
                        ? transferField.checked
                        : false;

                    if (
                        !unitName ||
                        !Number.isFinite(factorValue) ||
                        factorValue <= 0
                    ) {
                        hasInvalid = true;
                        return;
                    }

                    if (receivingDefault) {
                        if (receivingAssigned) {
                            receivingDefault = false;
                        } else {
                            receivingAssigned = true;
                        }
                    }

                    if (transferDefault) {
                        if (transferAssigned) {
                            transferDefault = false;
                        } else {
                            transferAssigned = true;
                        }
                    }

                    unitsPayload.push({
                        id: unitId,
                        name: unitName,
                        factor: factorValue,
                        receiving_default: receivingDefault,
                        transfer_default: transferDefault,
                        is_base: isBase,
                    });
                });

                if (hasInvalid || !unitsPayload.length) {
                    showManageUnitsError(
                        "Enter a valid name and ratio for each unit."
                    );
                    return;
                }

                const baseEntry = unitsPayload.find((unit) => unit.is_base);
                if (!baseEntry) {
                    showManageUnitsError("The base unit is required.");
                    return;
                }

                if (!receivingAssigned) {
                    baseEntry.receiving_default = true;
                    receivingAssigned = true;
                }

                if (!transferAssigned) {
                    baseEntry.transfer_default = true;
                    transferAssigned = true;
                }

                if (!receivingAssigned || !transferAssigned) {
                    showManageUnitsError(
                        "Choose defaults for receiving and transfers."
                    );
                    return;
                }

                const csrfTokenInput = document.querySelector(
                    'input[name="csrf_token"]'
                );
                const csrfToken = csrfTokenInput ? csrfTokenInput.value : null;
                const targetRow = manageUnitsTargetRow || null;
                const targetSelect = targetRow
                    ? targetRow.querySelector(".unit-select")
                    : null;
                const previousSelection = targetSelect
                    ? targetSelect.value
                    : null;

                fetch(`/items/${itemId}/units`, {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken || "",
                    },
                    body: JSON.stringify({ units: unitsPayload }),
                })
                    .then((response) => {
                        if (!response.ok) {
                            throw new Error("Failed to save units");
                        }
                        return response.json();
                    })
                    .then(() => {
                        if (manageUnitsModal) {
                            manageUnitsModal.hide();
                        }
                        if (targetRow && targetRow.isConnected) {
                            const unitSelect = targetRow.querySelector(
                                ".unit-select"
                            );
                            if (unitSelect) {
                                fetchUnits(
                                    itemId,
                                    unitSelect,
                                    previousSelection || null
                                );
                            }
                        }
                        if (!manageUnitsModal) {
                            manageUnitsCurrentItemId = null;
                            manageUnitsTargetRow = null;
                            resetManageUnitsRows();
                            showManageUnitsError("");
                        }
                    })
                    .catch(() => {
                        showManageUnitsError(
                            "Unable to save units. Please try again."
                        );
                    });
            });
        }

        if (supportsPointerEvents) {
            container.addEventListener("pointerdown", handleDragPrepare);
            container.addEventListener("pointerup", handlePointerLikeCleanup);
            container.addEventListener("pointercancel", handlePointerLikeCleanup);
            document.addEventListener("pointerup", handlePointerLikeCleanup);
        } else {
            container.addEventListener("mousedown", handleDragPrepare);
            container.addEventListener("touchstart", handleDragPrepare, {
                passive: true,
            });
            document.addEventListener("mouseup", handlePointerLikeCleanup);
            document.addEventListener("touchend", handlePointerLikeCleanup);
            document.addEventListener("touchcancel", handlePointerLikeCleanup);
        }

        container.addEventListener("dragstart", (event) => {
            const row = event.target.closest(".item-row");
            if (!row || row.dataset.dragPrepared !== "true") {
                event.preventDefault();
                return;
            }
            skipNextPointerCancel = false;
            if (pointerCancelFallbackTimer) {
                clearTimeout(pointerCancelFallbackTimer);
                pointerCancelFallbackTimer = null;
            }
            activeDragRow = row;
            row.classList.add("dragging");
            if (event.dataTransfer) {
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData(
                    "text/plain",
                    row.dataset.dragPrepared || ""
                );
            }
        });

        container.addEventListener("dragover", (event) => {
            if (!activeDragRow) {
                return;
            }
            event.preventDefault();
            if (event.dataTransfer) {
                event.dataTransfer.dropEffect = "move";
            }
            const targetRow = event.target.closest(".item-row");
            if (!targetRow || targetRow === activeDragRow) {
                return;
            }
            const rect = targetRow.getBoundingClientRect();
            const offset = event.clientY - rect.top;
            if (offset < rect.height / 2) {
                container.insertBefore(activeDragRow, targetRow);
            } else {
                container.insertBefore(
                    activeDragRow,
                    targetRow.nextElementSibling
                );
            }
        });

        container.addEventListener("drop", (event) => {
            if (!activeDragRow) {
                return;
            }
            event.preventDefault();
            updatePositions();
        });

        container.addEventListener("dragend", () => {
            if (!activeDragRow) {
                return;
            }
            const row = activeDragRow;
            row.classList.remove("dragging");
            activeDragRow = null;
            clearPreparedDragRow();
            updatePositions();
        });

        if (newItemUnitsContainer) {
            newItemUnitsContainer.addEventListener("click", (event) => {
                const target = event.target;
                if (!(target instanceof Element)) {
                    return;
                }
                const removeButton = target.closest(".new-item-unit-remove");
                if (!removeButton) {
                    return;
                }
                const row = removeButton.closest(".new-item-unit-row");
                if (row && row.dataset.base !== "true") {
                    row.remove();
                    ensureUnitDefaults();
                }
            });
        }

        if (quickAddButton && newItemModal) {
            quickAddButton.addEventListener("click", () => {
                const nameField = document.getElementById("new-item-name");
                if (nameField) {
                    nameField.value = "";
                }
                resetNewItemUnitRows();
                if (nameField) {
                    nameField.focus();
                }
                newItemModal.show();
            });
        }

        if (saveNewItemButton) {
            saveNewItemButton.addEventListener("click", () => {
                const nameInput = document.getElementById("new-item-name");
                const glCodeSelect = document.getElementById("new-item-gl-code");
                const csrfTokenInput = document.querySelector(
                    'input[name="csrf_token"]'
                );

                const name = nameInput ? nameInput.value.trim() : "";
                const glCode = glCodeSelect ? glCodeSelect.value : null;
                const baseUnit = baseUnitSelect ? baseUnitSelect.value : null;
                const csrfToken = csrfTokenInput ? csrfTokenInput.value : null;

                const unitRows = newItemUnitsContainer
                    ? Array.from(
                          newItemUnitsContainer.querySelectorAll(
                              ".new-item-unit-row"
                          )
                      )
                    : [];

                const unitsPayload = [];
                let hasInvalidUnits = false;
                let hasReceivingDefault = false;
                let hasTransferDefault = false;

                unitRows.forEach((row) => {
                    const nameField = row.querySelector(".new-item-unit-name");
                    const factorField = row.querySelector(".new-item-unit-factor");
                    const receivingField = row.querySelector(
                        ".new-item-unit-receiving"
                    );
                    const transferField = row.querySelector(
                        ".new-item-unit-transfer"
                    );

                    let unitName = nameField ? nameField.value.trim() : "";
                    let factorValue = factorField
                        ? window.NumericInput
                            ? window.NumericInput.parseValue(factorField)
                            : parseFloat(factorField.value)
                        : NaN;

                    if (row.dataset.base === "true") {
                        if (baseUnit) {
                            unitName = baseUnit;
                        }
                        factorValue = 1;
                    }

                    const receivingDefault = receivingField
                        ? receivingField.checked
                        : false;
                    const transferDefault = transferField
                        ? transferField.checked
                        : false;

                    if (!unitName || !Number.isFinite(factorValue) || factorValue <= 0) {
                        hasInvalidUnits = true;
                        return;
                    }

                    if (receivingDefault) {
                        hasReceivingDefault = true;
                    }
                    if (transferDefault) {
                        hasTransferDefault = true;
                    }

                    unitsPayload.push({
                        name: unitName,
                        factor: factorValue,
                        receiving_default: receivingDefault,
                        transfer_default: transferDefault,
                    });
                });

                if (
                    !name ||
                    !baseUnit ||
                    !glCode ||
                    !unitsPayload.length ||
                    hasInvalidUnits
                ) {
                    return;
                }

                const baseUnitEntry = unitsPayload.find(
                    (unit) => unit.name === baseUnit
                );
                if (!hasReceivingDefault && baseUnitEntry) {
                    baseUnitEntry.receiving_default = true;
                    hasReceivingDefault = true;
                }
                if (!hasTransferDefault && baseUnitEntry) {
                    baseUnitEntry.transfer_default = true;
                    hasTransferDefault = true;
                }

                if (!hasReceivingDefault || !hasTransferDefault) {
                    return;
                }

                fetch("/items/quick_add", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                        "X-CSRFToken": csrfToken || "",
                    },
                    body: JSON.stringify({
                        name,
                        purchase_gl_code: glCode,
                        base_unit: baseUnit,
                        units: unitsPayload,
                    }),
                })
                    .then((response) => {
                        if (!response.ok) {
                            throw new Error("Unable to create item");
                        }
                        return response.json();
                    })
                    .then((data) => {
                        if (!data || !data.id) {
                            return;
                        }
                        const row = addRow({
                            itemId: data.id,
                            itemName: data.name,
                            glCode: data.gl_code || "",
                        });
                        const unitSelect = row.querySelector(".unit-select");
                        fetchUnits(data.id, unitSelect);
                        const quantityInput = row.querySelector(".quantity");
                        if (quantityInput) {
                            quantityInput.focus();
                        }

                        if (nameInput) {
                            nameInput.value = "";
                        }
                        resetNewItemUnitRows();

                        if (newItemModal) {
                            newItemModal.hide();
                        }
                    })
                    .catch(() => {
                        /* Silent failure keeps UI responsive */
                    });
            });
        }

        container.addEventListener("input", (event) => {
            if (event.target.classList.contains("item-search")) {
                handleSearchInput(event.target);
            }
        });

        container.addEventListener("focusin", (event) => {
            if (event.target.classList.contains("item-search")) {
                const row = event.target.closest(".item-row");
                if (!row) {
                    return;
                }
                const suggestionList = row.querySelector(".suggestion-list");
                if (suggestionList && suggestionList.children.length) {
                    closeAllSuggestionLists(suggestionList);
                    suggestionList.classList.remove("d-none");
                }
            }
        });

        container.addEventListener("keydown", (event) => {
            if (event.target.classList.contains("item-search")) {
                handleSearchKeydown(event);
            } else if (event.target.classList.contains("quantity")) {
                handleQuantityKeydown(event);
            }
        });

        container.addEventListener("click", (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }
            if (target.closest(".manage-units-button")) {
                const button = target.closest(".manage-units-button");
                const row = button ? button.closest(".item-row") : null;
                if (row) {
                    openManageUnitsForRow(row);
                }
            } else if (target.classList.contains("remove-item")) {
                const row = target.closest(".item-row");
                if (row) {
                    row.remove();
                    updatePositions();
                }
            } else if (target.classList.contains("suggestion-option")) {
                handleSuggestionSelection(target);
            }
        });

        document.addEventListener("click", (event) => {
            if (!container.contains(event.target)) {
                closeAllSuggestionLists();
            }
        });

        Array.from(container.querySelectorAll(".item-row")).forEach((row) => {
            const hiddenField = row.querySelector(".item-id-field");
            const unitSelect = row.querySelector(".unit-select");
            const selectedUnit = unitSelect ? unitSelect.dataset.selected : null;
            setManageButtonState(row, Boolean(hiddenField && hiddenField.value));
            if (hiddenField && hiddenField.value) {
                fetchUnits(hiddenField.value, unitSelect, selectedUnit || null);
            }
        });

        updatePositions();

        return {
            addRow,
        };
    }

    window.initPurchaseOrderForm = initPurchaseOrderForm;
})();
