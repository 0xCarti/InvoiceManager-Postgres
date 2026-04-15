(function () {
    var container = document.querySelector("[data-board-blocks]");
    var addButton = document.getElementById("add-board-block");
    var template = document.getElementById("board-block-template");
    var canvas = document.querySelector("[data-board-editor-canvas]");
    var gridColumns = canvas ? parseInt(canvas.getAttribute("data-grid-columns") || "24", 10) : 24;
    var gridRows = canvas ? parseInt(canvas.getAttribute("data-grid-rows") || "12", 10) : 12;
    var activeRow = null;
    var pointerState = null;

    if (!container || !addButton || !template || !canvas) {
        return;
    }

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
        return row.querySelector(selector);
    }

    function getBlockState(row) {
        return {
            typeField: getField(row, ".board-block-type"),
            titleField: getField(row, ".board-block-title-input"),
            bodyField: getField(row, ".board-block-body-input"),
            mediaAssetField: getField(row, ".board-block-media-asset"),
            mediaUrlField: getField(row, ".board-block-media-url"),
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
        updateCoordinateSummary(row);
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

    function updateCoordinateSummary(row) {
        var values = getGridValues(row);
        var summary = row.querySelector("[data-block-coordinates]");
        if (summary) {
            summary.textContent = (
                "Position " + values.x + ", " + values.y +
                " / Size " + values.width + " x " + values.height
            );
        }
    }

    function updateRowState(row) {
        var fields = getBlockState(row);
        var blockType = fields.typeField ? fields.typeField.value : "menu";
        var menuFields = row.querySelectorAll(".board-block-menu-fields");
        var bodyFields = row.querySelectorAll(".board-block-body-field");
        var mediaFields = row.querySelectorAll(".board-block-media-field");
        var index;

        for (index = 0; index < menuFields.length; index += 1) {
            menuFields[index].style.display = blockType === "menu" ? "" : "none";
        }
        for (index = 0; index < bodyFields.length; index += 1) {
            bodyFields[index].style.display = blockType === "text" ? "" : "none";
        }
        for (index = 0; index < mediaFields.length; index += 1) {
            mediaFields[index].style.display = blockType === "image" || blockType === "video" ? "" : "none";
        }
        updateCoordinateSummary(row);
    }

    function selectRow(row) {
        var rows = getRows();
        var index;
        activeRow = row || null;
        for (index = 0; index < rows.length; index += 1) {
            rows[index].classList.toggle("is-selected", rows[index] === activeRow);
        }
        renderPreview();
    }

    function blockPreviewTitle(row) {
        var fields = getBlockState(row);
        var title = fields.titleField ? fields.titleField.value.trim() : "";
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
            return fields.bodyField && fields.bodyField.value.trim()
                ? fields.bodyField.value.trim().slice(0, 40)
                : "Text and announcements";
        }
        if (fields.mediaAssetField && fields.mediaAssetField.value && fields.mediaAssetField.value !== "0") {
            return "Uses media library asset";
        }
        if (fields.mediaUrlField && fields.mediaUrlField.value.trim()) {
            return "Uses external media URL";
        }
        return "No media selected yet";
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
            writeGridValues(
                pointerState.row,
                nextX,
                nextY,
                pointerState.startValues.width,
                pointerState.startValues.height
            );
        } else {
            nextWidth = pointerState.startValues.width + deltaColumns;
            nextHeight = pointerState.startValues.height + deltaRows;
            writeGridValues(
                pointerState.row,
                pointerState.startValues.x,
                pointerState.startValues.y,
                nextWidth,
                nextHeight
            );
        }

        renderPreview();
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
            block.setAttribute("data-row-index", String(index));

            handle = document.createElement("div");
            handle.className = "board-editor-block-handle";
            handle.innerHTML = (
                "<span>" + blockTypeLabel(fields.typeField ? fields.typeField.value : "menu") + "</span>" +
                "<span>" + values.width + " x " + values.height + "</span>"
            );
            handle.addEventListener("pointerdown", function (targetRow) {
                return function (event) {
                    event.preventDefault();
                    startPointerInteraction(targetRow, "move", event);
                };
            }(row));

            body = document.createElement("div");
            body.className = "board-editor-block-body";
            body.innerHTML = (
                '<div class="board-editor-block-title">' + blockPreviewTitle(row) + "</div>" +
                '<div class="board-editor-block-meta">' + blockPreviewMeta(row) + "</div>"
            );
            body.addEventListener("click", function (targetRow) {
                return function () {
                    selectRow(targetRow);
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

    function reindexAttributes(row, index) {
        var fields;
        var labels;
        var rowTitle;
        var regex = /blocks-(?:__prefix__|\d+)-/g;
        var replacement = "blocks-" + String(index) + "-";
        var fieldIndex;

        rowTitle = row.querySelector("[data-block-label]");
        if (rowTitle) {
            rowTitle.textContent = "Block " + String(index + 1);
        }

        fields = row.querySelectorAll("[name], [id]");
        for (fieldIndex = 0; fieldIndex < fields.length; fieldIndex += 1) {
            if (fields[fieldIndex].getAttribute("name")) {
                fields[fieldIndex].setAttribute(
                    "name",
                    fields[fieldIndex].getAttribute("name").replace(regex, replacement)
                );
            }
            if (fields[fieldIndex].getAttribute("id")) {
                fields[fieldIndex].setAttribute(
                    "id",
                    fields[fieldIndex].getAttribute("id").replace(regex, replacement)
                );
            }
        }

        labels = row.querySelectorAll("label[for]");
        for (fieldIndex = 0; fieldIndex < labels.length; fieldIndex += 1) {
            labels[fieldIndex].setAttribute(
                "for",
                labels[fieldIndex].getAttribute("for").replace(regex, replacement)
            );
        }
    }

    function refreshRows() {
        var rows = getRows();
        var index;
        var moveUp;
        var moveDown;

        for (index = 0; index < rows.length; index += 1) {
            reindexAttributes(rows[index], index);
            updateRowState(rows[index]);
            moveUp = rows[index].querySelector("[data-move-block-up]");
            moveDown = rows[index].querySelector("[data-move-block-down]");
            if (moveUp) {
                moveUp.disabled = index === 0;
            }
            if (moveDown) {
                moveDown.disabled = index === rows.length - 1;
            }
        }

        if (activeRow && rows.indexOf(activeRow) === -1) {
            activeRow = rows.length ? rows[0] : null;
        }
        container.setAttribute("data-next-index", String(rows.length));
        renderPreview();
    }

    function bindLivePreview(row) {
        row.addEventListener("input", function () {
            updateRowState(row);
            renderPreview();
        });
        row.addEventListener("change", function () {
            updateRowState(row);
            renderPreview();
        });
        row.addEventListener("click", function () {
            selectRow(row);
        });
    }

    function bindRow(row) {
        var blockTypeField = row.querySelector(".board-block-type");
        var removeButton = row.querySelector("[data-remove-board-block]");
        var moveUpButton = row.querySelector("[data-move-block-up]");
        var moveDownButton = row.querySelector("[data-move-block-down]");

        if (blockTypeField) {
            blockTypeField.addEventListener("change", function () {
                updateRowState(row);
                renderPreview();
            });
        }

        if (removeButton) {
            removeButton.addEventListener("click", function () {
                if (row.parentNode) {
                    row.parentNode.removeChild(row);
                }
                refreshRows();
            });
        }

        if (moveUpButton) {
            moveUpButton.addEventListener("click", function () {
                var previous = row.previousElementSibling;
                if (!previous) {
                    return;
                }
                container.insertBefore(row, previous);
                refreshRows();
            });
        }

        if (moveDownButton) {
            moveDownButton.addEventListener("click", function () {
                var next = row.nextElementSibling;
                if (!next) {
                    return;
                }
                container.insertBefore(next, row);
                refreshRows();
            });
        }

        bindLivePreview(row);
        updateRowState(row);
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
        bindRow(newRow);
        seedNewRowLayout(newRow, nextIndex);
        selectRow(newRow);
        refreshRows();
    });

    Array.prototype.forEach.call(getRows(), function (row, index) {
        bindRow(row);
        if (!getField(row, ".block-grid-x").value) {
            seedNewRowLayout(row, index);
        } else {
            syncLegacyWidth(row);
            updateCoordinateSummary(row);
        }
    });

    if (getRows().length) {
        selectRow(getRows()[0]);
    } else {
        renderPreview();
    }
    refreshRows();
}());
