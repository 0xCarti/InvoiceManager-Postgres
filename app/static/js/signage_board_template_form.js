(function () {
    var container = document.querySelector("[data-board-blocks]");
    var addButton = document.getElementById("add-board-block");
    var template = document.getElementById("board-block-template");
    var canvas = document.querySelector("[data-board-editor-canvas]");
    var blockList = document.querySelector("[data-board-block-list]");
    var modalElement = document.getElementById("board-block-settings-modal");
    var gridColumns = canvas ? parseInt(canvas.getAttribute("data-grid-columns") || "24", 10) : 24;
    var gridRows = canvas ? parseInt(canvas.getAttribute("data-grid-rows") || "12", 10) : 12;
    var modal = null;
    var activeRow = null;
    var modalRow = null;
    var pointerState = null;
    var modalFields;

    if (!container || !addButton || !template || !canvas || !blockList || !modalElement) {
        return;
    }

    if (window.bootstrap && window.bootstrap.Modal) {
        modal = new window.bootstrap.Modal(modalElement);
    }

    modalFields = {
        titleText: modalElement.querySelector("[data-board-block-modal-title]"),
        subtitleText: modalElement.querySelector("[data-board-block-modal-subtitle]"),
        errors: modalElement.querySelector("[data-board-block-modal-errors]"),
        typeField: modalElement.querySelector("[data-modal-block-type]"),
        titleField: modalElement.querySelector("[data-modal-block-title]"),
        menuColumnsField: modalElement.querySelector("[data-modal-menu-columns]"),
        menuRowsField: modalElement.querySelector("[data-modal-menu-rows]"),
        selectedProductsField: modalElement.querySelector("[data-modal-selected-product-ids]"),
        bodyField: modalElement.querySelector("[data-modal-block-body]"),
        mediaAssetField: modalElement.querySelector("[data-modal-media-asset-id]"),
        mediaUrlField: modalElement.querySelector("[data-modal-media-url]"),
        showTitleField: modalElement.querySelector("[data-modal-show-title]"),
        showPricesField: modalElement.querySelector("[data-modal-show-prices]"),
        showMenuDescriptionField: modalElement.querySelector("[data-modal-show-menu-description]"),
        menuSections: modalElement.querySelectorAll("[data-modal-menu-fields]"),
        bodySections: modalElement.querySelectorAll("[data-modal-body-field]"),
        mediaSections: modalElement.querySelectorAll("[data-modal-media-fields]"),
        applyButton: modalElement.querySelector("[data-board-block-modal-apply]")
    };

    function getRows() {
        return Array.prototype.slice.call(container.querySelectorAll("[data-board-block]"));
    }

    function parseIntSafe(value, fallback) {
        var number = parseInt(value, 10);
        return isNaN(number) ? fallback : number;
    }

    function clamp(value, min, max) {
        return Math.max(min, Math.min(max, value));
    }

    function escapeHtml(value) {
        return String(value || "")
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;")
            .replace(/'/g, "&#39;");
    }

    function blockTypeLabel(blockType) {
        if (blockType === "menu") {
            return "Menu";
        }
        if (blockType === "text") {
            return "Text";
        }
        if (blockType === "image") {
            return "Image";
        }
        if (blockType === "video") {
            return "Video";
        }
        return "Block";
    }

    function getField(row, selector) {
        return row ? row.querySelector(selector) : null;
    }

    function getBlockState(row) {
        return {
            typeField: getField(row, ".board-block-type"),
            titleField: getField(row, ".board-block-title-input"),
            bodyField: getField(row, ".board-block-body-input"),
            mediaAssetField: getField(row, ".board-block-media-asset"),
            mediaUrlField: getField(row, ".board-block-media-url"),
            menuColumnsField: getField(row, ".board-block-menu-columns"),
            menuRowsField: getField(row, ".board-block-menu-rows"),
            selectedProductsField: getField(row, ".board-block-selected-products"),
            showTitleField: getField(row, ".board-block-show-title"),
            showPricesField: getField(row, ".board-block-show-prices"),
            showMenuDescriptionField: getField(row, ".board-block-show-menu-description"),
            widthUnitsField: getField(row, ".board-block-width-units"),
            gridXField: getField(row, ".block-grid-x"),
            gridYField: getField(row, ".block-grid-y"),
            gridWidthField: getField(row, ".block-grid-width"),
            gridHeightField: getField(row, ".block-grid-height")
        };
    }

    function getGridValues(row) {
        var fields = getBlockState(row);
        return {
            x: clamp(parseIntSafe(fields.gridXField && fields.gridXField.value, 1), 1, gridColumns),
            y: clamp(parseIntSafe(fields.gridYField && fields.gridYField.value, 1), 1, gridRows),
            width: clamp(parseIntSafe(fields.gridWidthField && fields.gridWidthField.value, 12), 1, gridColumns),
            height: clamp(parseIntSafe(fields.gridHeightField && fields.gridHeightField.value, 10), 1, gridRows)
        };
    }

    function syncLegacyWidth(row) {
        var fields = getBlockState(row);
        var values = getGridValues(row);
        if (fields.widthUnitsField) {
            fields.widthUnitsField.value = String(clamp(Math.round(values.width / 2), 1, 12));
        }
    }

    function writeGridValues(row, x, y, width, height) {
        var fields = getBlockState(row);
        var clampedWidth = clamp(width, 1, gridColumns);
        var clampedHeight = clamp(height, 1, gridRows);
        var clampedX = clamp(x, 1, Math.max(gridColumns - clampedWidth + 1, 1));
        var clampedY = clamp(y, 1, Math.max(gridRows - clampedHeight + 1, 1));

        if (fields.gridXField) {
            fields.gridXField.value = String(clampedX);
        }
        if (fields.gridYField) {
            fields.gridYField.value = String(clampedY);
        }
        if (fields.gridWidthField) {
            fields.gridWidthField.value = String(clampedWidth);
        }
        if (fields.gridHeightField) {
            fields.gridHeightField.value = String(clampedHeight);
        }
        syncLegacyWidth(row);
    }

    function defaultPlacement(index, blockType) {
        var placements = [
            { x: 1, y: 1, width: 16, height: 10 },
            { x: 17, y: 1, width: 8, height: 5 },
            { x: 17, y: 6, width: 8, height: 5 },
            { x: 1, y: 1, width: 12, height: 6 },
            { x: 13, y: 1, width: 12, height: 6 },
            { x: 1, y: 7, width: 12, height: 6 }
        ];
        var placement = placements[index] || {
            x: 1 + ((index * 4) % 12),
            y: 1 + (Math.floor(index / 2) % 6),
            width: blockType === "menu" ? 12 : 8,
            height: blockType === "menu" ? 6 : 4
        };
        if (blockType !== "menu" && placement.width > 10) {
            placement.width = 8;
            placement.height = 4;
        }
        return placement;
    }

    function rowHasErrors(row) {
        return !!row.querySelector(".text-danger");
    }

    function getRowErrors(row) {
        var nodes = row.querySelectorAll(".text-danger");
        var errors = [];
        var index;
        var text;
        for (index = 0; index < nodes.length; index += 1) {
            text = (nodes[index].textContent || "").replace(/\s+/g, " ").trim();
            if (text && errors.indexOf(text) === -1) {
                errors.push(text);
            }
        }
        return errors;
    }

    function clearRowErrors(row) {
        var nodes = row.querySelectorAll(".text-danger");
        var index;
        for (index = nodes.length - 1; index >= 0; index -= 1) {
            if (nodes[index].parentNode) {
                nodes[index].parentNode.removeChild(nodes[index]);
            }
        }
    }

    function getSelectedValues(selectField) {
        var values = [];
        var options;
        var index;
        if (!selectField) {
            return values;
        }
        options = selectField.options || [];
        for (index = 0; index < options.length; index += 1) {
            if (options[index].selected) {
                values.push(String(options[index].value));
            }
        }
        return values;
    }

    function setSelectedValues(selectField, values) {
        var options;
        var index;
        var normalized = [];
        if (!selectField) {
            return;
        }
        options = selectField.options || [];
        for (index = 0; index < values.length; index += 1) {
            normalized.push(String(values[index]));
        }
        for (index = 0; index < options.length; index += 1) {
            options[index].selected = normalized.indexOf(String(options[index].value)) !== -1;
        }
    }

    function updateModalVisibility() {
        var blockType = modalFields.typeField ? modalFields.typeField.value : "menu";
        var index;
        for (index = 0; index < modalFields.menuSections.length; index += 1) {
            modalFields.menuSections[index].style.display = blockType === "menu" ? "" : "none";
        }
        for (index = 0; index < modalFields.bodySections.length; index += 1) {
            modalFields.bodySections[index].style.display = blockType === "text" ? "" : "none";
        }
        for (index = 0; index < modalFields.mediaSections.length; index += 1) {
            modalFields.mediaSections[index].style.display = blockType === "image" || blockType === "video" ? "" : "none";
        }
    }

    function updateRowState(row) {
        syncLegacyWidth(row);
    }

    function blockPreviewTitle(row) {
        var fields = getBlockState(row);
        var title = fields.titleField ? fields.titleField.value.replace(/\s+/g, " ").trim() : "";
        if (title) {
            return title;
        }
        return blockTypeLabel(fields.typeField ? fields.typeField.value : "menu") + " Block";
    }

    function blockPreviewMeta(row) {
        var fields = getBlockState(row);
        var blockType = fields.typeField ? fields.typeField.value : "menu";
        if (blockType === "menu") {
            return "Menu data block";
        }
        if (blockType === "text") {
            return fields.bodyField && fields.bodyField.value.replace(/\s+/g, " ").trim()
                ? fields.bodyField.value.replace(/\s+/g, " ").trim().slice(0, 48)
                : "Text and announcements";
        }
        if (fields.mediaAssetField && fields.mediaAssetField.value && fields.mediaAssetField.value !== "0") {
            return "Uses media library asset";
        }
        if (fields.mediaUrlField && fields.mediaUrlField.value.replace(/\s+/g, " ").trim()) {
            return "Uses external media URL";
        }
        return "No media selected yet";
    }

    function formatCoordinates(row) {
        var values = getGridValues(row);
        return "Position " + values.x + ", " + values.y + " / Size " + values.width + " x " + values.height;
    }

    function renderModalErrors(row) {
        var errors = row ? getRowErrors(row) : [];
        var html = "";
        var index;
        if (!modalFields.errors) {
            return;
        }
        if (!errors.length) {
            modalFields.errors.classList.add("d-none");
            modalFields.errors.innerHTML = "";
            return;
        }
        html = "<ul class=\"mb-0\">";
        for (index = 0; index < errors.length; index += 1) {
            html += "<li>" + escapeHtml(errors[index]) + "</li>";
        }
        html += "</ul>";
        modalFields.errors.innerHTML = html;
        modalFields.errors.classList.remove("d-none");
    }

    function renderBoardState() {
        renderPreview();
        renderBlockList();
    }

    function selectRow(row) {
        activeRow = row || null;
        renderBoardState();
    }

    function populateModalFromRow(row) {
        var fields = getBlockState(row);
        modalFields.typeField.value = fields.typeField ? fields.typeField.value : "menu";
        modalFields.titleField.value = fields.titleField ? fields.titleField.value : "";
        modalFields.menuColumnsField.value = fields.menuColumnsField ? fields.menuColumnsField.value : "2";
        modalFields.menuRowsField.value = fields.menuRowsField ? fields.menuRowsField.value : "4";
        modalFields.bodyField.value = fields.bodyField ? fields.bodyField.value : "";
        modalFields.mediaAssetField.value = fields.mediaAssetField ? fields.mediaAssetField.value : "0";
        modalFields.mediaUrlField.value = fields.mediaUrlField ? fields.mediaUrlField.value : "";
        modalFields.showTitleField.checked = !!(fields.showTitleField && fields.showTitleField.checked);
        modalFields.showPricesField.checked = !!(fields.showPricesField && fields.showPricesField.checked);
        modalFields.showMenuDescriptionField.checked = !!(fields.showMenuDescriptionField && fields.showMenuDescriptionField.checked);
        setSelectedValues(modalFields.selectedProductsField, getSelectedValues(fields.selectedProductsField));
        if (modalFields.titleText) {
            modalFields.titleText.textContent = blockPreviewTitle(row) + " Settings";
        }
        if (modalFields.subtitleText) {
            modalFields.subtitleText.textContent = blockTypeLabel(modalFields.typeField.value) + " - " + formatCoordinates(row);
        }
        updateModalVisibility();
        renderModalErrors(row);
    }

    function applyModalToRow(row) {
        var fields = getBlockState(row);
        if (fields.typeField) {
            fields.typeField.value = modalFields.typeField.value;
        }
        if (fields.titleField) {
            fields.titleField.value = modalFields.titleField.value;
        }
        if (fields.menuColumnsField) {
            fields.menuColumnsField.value = modalFields.menuColumnsField.value;
        }
        if (fields.menuRowsField) {
            fields.menuRowsField.value = modalFields.menuRowsField.value;
        }
        if (fields.bodyField) {
            fields.bodyField.value = modalFields.bodyField.value;
        }
        if (fields.mediaAssetField) {
            fields.mediaAssetField.value = modalFields.mediaAssetField.value;
        }
        if (fields.mediaUrlField) {
            fields.mediaUrlField.value = modalFields.mediaUrlField.value;
        }
        if (fields.showTitleField) {
            fields.showTitleField.checked = !!modalFields.showTitleField.checked;
        }
        if (fields.showPricesField) {
            fields.showPricesField.checked = !!modalFields.showPricesField.checked;
        }
        if (fields.showMenuDescriptionField) {
            fields.showMenuDescriptionField.checked = !!modalFields.showMenuDescriptionField.checked;
        }
        setSelectedValues(fields.selectedProductsField, getSelectedValues(modalFields.selectedProductsField));
        clearRowErrors(row);
        updateRowState(row);
    }

    function openRowModal(row) {
        if (!modal) {
            return;
        }
        modalRow = row;
        selectRow(row);
        populateModalFromRow(row);
        modal.show();
    }

    function moveRowUp(row) {
        var previous = row.previousElementSibling;
        if (!previous) {
            return;
        }
        container.insertBefore(row, previous);
        refreshRows();
        selectRow(row);
    }

    function moveRowDown(row) {
        var next = row.nextElementSibling;
        if (!next) {
            return;
        }
        container.insertBefore(next, row);
        refreshRows();
        selectRow(row);
    }

    function removeRow(row) {
        var rows = getRows();
        var index = rows.indexOf(row);
        var nextActive = null;
        if (index > 0) {
            nextActive = rows[index - 1];
        } else if (rows.length > 1) {
            nextActive = rows[1];
        }
        if (row.parentNode) {
            row.parentNode.removeChild(row);
        }
        refreshRows();
        if (nextActive && getRows().indexOf(nextActive) !== -1) {
            selectRow(nextActive);
        } else if (getRows().length) {
            selectRow(getRows()[0]);
        } else {
            selectRow(null);
        }
    }

    function startPointerInteraction(row, mode, event) {
        var values = getGridValues(row);
        selectRow(row);
        pointerState = {
            row: row,
            mode: mode,
            startClientX: event.clientX,
            startClientY: event.clientY,
            startValues: values
        };
        document.addEventListener("pointermove", handlePointerMove);
        document.addEventListener("pointerup", stopPointerInteraction);
        document.addEventListener("pointercancel", stopPointerInteraction);
    }

    function handlePointerMove(event) {
        var rect;
        var columnSize;
        var rowSize;
        var deltaColumns;
        var deltaRows;
        var nextX;
        var nextY;
        var nextWidth;
        var nextHeight;

        if (!pointerState) {
            return;
        }

        rect = canvas.getBoundingClientRect();
        columnSize = rect.width / gridColumns;
        rowSize = rect.height / gridRows;
        deltaColumns = Math.round((event.clientX - pointerState.startClientX) / columnSize);
        deltaRows = Math.round((event.clientY - pointerState.startClientY) / rowSize);

        if (pointerState.mode === "move") {
            nextX = pointerState.startValues.x + deltaColumns;
            nextY = pointerState.startValues.y + deltaRows;
            writeGridValues(pointerState.row, nextX, nextY, pointerState.startValues.width, pointerState.startValues.height);
        } else {
            nextWidth = pointerState.startValues.width + deltaColumns;
            nextHeight = pointerState.startValues.height + deltaRows;
            writeGridValues(pointerState.row, pointerState.startValues.x, pointerState.startValues.y, nextWidth, nextHeight);
        }

        renderBoardState();
    }

    function stopPointerInteraction() {
        if (!pointerState) {
            return;
        }
        document.removeEventListener("pointermove", handlePointerMove);
        document.removeEventListener("pointerup", stopPointerInteraction);
        document.removeEventListener("pointercancel", stopPointerInteraction);
        pointerState = null;
    }

    function renderPreview() {
        var rows = getRows();
        var index;
        var row;
        var values;
        var fields;
        var block;
        var handle;
        var body;
        var resizeHandle;

        canvas.innerHTML = "";
        for (index = 0; index < rows.length; index += 1) {
            row = rows[index];
            fields = getBlockState(row);
            values = getGridValues(row);

            block = document.createElement("div");
            block.className = "board-editor-block is-" + (fields.typeField ? fields.typeField.value : "menu");
            if (row === activeRow) {
                block.className += " is-selected";
            }
            block.style.left = (((values.x - 1) / gridColumns) * 100) + "%";
            block.style.top = (((values.y - 1) / gridRows) * 100) + "%";
            block.style.width = ((values.width / gridColumns) * 100) + "%";
            block.style.height = ((values.height / gridRows) * 100) + "%";

            handle = document.createElement("div");
            handle.className = "board-editor-block-handle";
            handle.innerHTML = "<span>" + blockTypeLabel(fields.typeField ? fields.typeField.value : "menu") + "</span><span>" + values.width + " x " + values.height + "</span>";
            handle.addEventListener("pointerdown", function (targetRow) {
                return function (event) {
                    event.preventDefault();
                    startPointerInteraction(targetRow, "move", event);
                };
            }(row));

            body = document.createElement("div");
            body.className = "board-editor-block-body";
            body.innerHTML = '<div class="board-editor-block-title">' + escapeHtml(blockPreviewTitle(row)) + "</div><div class=\"board-editor-block-meta\">" + escapeHtml(blockPreviewMeta(row)) + "</div>";
            body.addEventListener("click", function (targetRow) {
                return function () {
                    openRowModal(targetRow);
                };
            }(row));

            resizeHandle = document.createElement("div");
            resizeHandle.className = "board-editor-block-resize";
            resizeHandle.addEventListener("pointerdown", function (targetRow) {
                return function (event) {
                    event.preventDefault();
                    startPointerInteraction(targetRow, "resize", event);
                };
            }(row));

            block.appendChild(handle);
            block.appendChild(body);
            block.appendChild(resizeHandle);
            canvas.appendChild(block);
        }
    }

    function renderBlockList() {
        var rows = getRows();
        var index;
        var row;
        var item;
        var values;
        var title;
        var actionRow;
        var moveUpButton;
        var moveDownButton;
        var editButton;
        var removeButton;
        var fields;

        blockList.innerHTML = "";
        for (index = 0; index < rows.length; index += 1) {
            row = rows[index];
            values = getGridValues(row);
            title = blockPreviewTitle(row);
            fields = getBlockState(row);

            item = document.createElement("div");
            item.className = "board-block-list-item p-3";
            if (row === activeRow) {
                item.className += " is-selected";
            }

            item.innerHTML =
                '<div class="d-flex flex-column flex-lg-row justify-content-between align-items-lg-start gap-3">' +
                    '<div>' +
                        '<div class="d-flex flex-wrap align-items-center gap-2 mb-1">' +
                            '<span class="badge text-bg-dark">Block ' + String(index + 1) + "</span>" +
                            '<span class="badge text-bg-secondary">' + escapeHtml(blockTypeLabel(fields.typeField ? fields.typeField.value : "menu")) + "</span>" +
                            (rowHasErrors(row) ? '<span class="badge text-bg-danger">Needs Attention</span>' : "") +
                        "</div>" +
                        '<div class="fw-semibold">' + escapeHtml(title) + "</div>" +
                        '<div class="text-muted small">' + escapeHtml(blockPreviewMeta(row)) + "</div>" +
                        '<div class="text-muted small mt-1">Grid ' + values.x + ", " + values.y + " / " + values.width + " x " + values.height + "</div>" +
                    "</div>" +
                "</div>";

            actionRow = document.createElement("div");
            actionRow.className = "d-flex flex-wrap gap-2 mt-3";

            editButton = document.createElement("button");
            editButton.type = "button";
            editButton.className = "btn btn-outline-primary btn-sm";
            editButton.textContent = "Edit Settings";
            editButton.addEventListener("click", function (targetRow) {
                return function (event) {
                    event.stopPropagation();
                    openRowModal(targetRow);
                };
            }(row));

            moveUpButton = document.createElement("button");
            moveUpButton.type = "button";
            moveUpButton.className = "btn btn-outline-secondary btn-sm";
            moveUpButton.textContent = "Up";
            moveUpButton.disabled = index === 0;
            moveUpButton.addEventListener("click", function (targetRow) {
                return function (event) {
                    event.stopPropagation();
                    moveRowUp(targetRow);
                };
            }(row));

            moveDownButton = document.createElement("button");
            moveDownButton.type = "button";
            moveDownButton.className = "btn btn-outline-secondary btn-sm";
            moveDownButton.textContent = "Down";
            moveDownButton.disabled = index === rows.length - 1;
            moveDownButton.addEventListener("click", function (targetRow) {
                return function (event) {
                    event.stopPropagation();
                    moveRowDown(targetRow);
                };
            }(row));

            removeButton = document.createElement("button");
            removeButton.type = "button";
            removeButton.className = "btn btn-outline-danger btn-sm";
            removeButton.textContent = "Remove";
            removeButton.addEventListener("click", function (targetRow) {
                return function (event) {
                    event.stopPropagation();
                    removeRow(targetRow);
                };
            }(row));

            item.addEventListener("click", function (targetRow) {
                return function () {
                    selectRow(targetRow);
                };
            }(row));

            actionRow.appendChild(editButton);
            actionRow.appendChild(moveUpButton);
            actionRow.appendChild(moveDownButton);
            actionRow.appendChild(removeButton);
            item.appendChild(actionRow);
            blockList.appendChild(item);
        }

        if (!rows.length) {
            blockList.innerHTML = '<div class="border rounded-4 p-4 text-muted small">No blocks yet. Add one to start laying out the board.</div>';
        }
    }

    function reindexAttributes(row, index) {
        var fields;
        var regex = /blocks-(?:__prefix__|\d+)-/g;
        var replacement = "blocks-" + String(index) + "-";
        var fieldIndex;

        fields = row.querySelectorAll("[name], [id], label[for]");
        for (fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
            if (fields[fieldIndex].getAttribute("name")) {
                fields[fieldIndex].setAttribute("name", fields[fieldIndex].getAttribute("name").replace(regex, replacement));
            }
            if (fields[fieldIndex].getAttribute("id")) {
                fields[fieldIndex].setAttribute("id", fields[fieldIndex].getAttribute("id").replace(regex, replacement));
            }
            if (fields[fieldIndex].getAttribute("for")) {
                fields[fieldIndex].setAttribute("for", fields[fieldIndex].getAttribute("for").replace(regex, replacement));
            }
        }
    }

    function refreshRows() {
        var rows = getRows();
        var index;

        for (index = 0; index < rows.length; index += 1) {
            reindexAttributes(rows[index], index);
            updateRowState(rows[index]);
        }

        if (activeRow && rows.indexOf(activeRow) === -1) {
            activeRow = rows.length ? rows[0] : null;
        }
        container.setAttribute("data-next-index", String(rows.length));
        renderBoardState();
    }

    function seedNewRowLayout(row, index) {
        var fields = getBlockState(row);
        var blockType = fields.typeField ? fields.typeField.value : "menu";
        var placement = defaultPlacement(index, blockType);
        writeGridValues(row, placement.x, placement.y, placement.width, placement.height);
    }

    addButton.addEventListener("click", function () {
        var nextIndex = parseInt(container.getAttribute("data-next-index") || "0", 10);
        var html = template.innerHTML.replace(/__prefix__/g, String(nextIndex));
        var wrapper = document.createElement("div");
        var newRow;

        wrapper.innerHTML = html;
        newRow = wrapper.children[0];
        if (!newRow) {
            return;
        }
        container.appendChild(newRow);
        seedNewRowLayout(newRow, nextIndex);
        refreshRows();
        openRowModal(newRow);
    });

    if (modalFields.typeField) {
        modalFields.typeField.addEventListener("change", function () {
            updateModalVisibility();
            if (modalRow && modalFields.subtitleText) {
                modalFields.subtitleText.textContent = blockTypeLabel(modalFields.typeField.value) + " - " + formatCoordinates(modalRow);
            }
        });
    }

    if (modalFields.applyButton) {
        modalFields.applyButton.addEventListener("click", function () {
            if (!modalRow) {
                return;
            }
            applyModalToRow(modalRow);
            refreshRows();
            selectRow(modalRow);
            if (modal) {
                modal.hide();
            }
        });
    }

    modalElement.addEventListener("hidden.bs.modal", function () {
        modalRow = null;
        renderModalErrors(null);
    });

    Array.prototype.forEach.call(getRows(), function (row, index) {
        if (!getField(row, ".block-grid-x").value) {
            seedNewRowLayout(row, index);
        } else {
            syncLegacyWidth(row);
        }
    });

    if (getRows().length) {
        selectRow(getRows()[0]);
    } else {
        renderBoardState();
    }
    refreshRows();

    Array.prototype.some.call(getRows(), function (row) {
        if (rowHasErrors(row)) {
            openRowModal(row);
            return true;
        }
        return false;
    });
}());
