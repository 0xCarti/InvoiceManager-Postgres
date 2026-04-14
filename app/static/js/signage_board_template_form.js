(function () {
    var container = document.querySelector("[data-board-blocks]");
    var addButton = document.getElementById("add-board-block");
    var template = document.getElementById("board-block-template");

    if (!container || !addButton || !template) {
        return;
    }

    function toggleCollection(nodes, visible) {
        var index;
        for (index = 0; index < nodes.length; index += 1) {
            nodes[index].style.display = visible ? "" : "none";
        }
    }

    function updateRowState(row) {
        var blockTypeField = row.querySelector(".board-block-type");
        var blockType = blockTypeField ? blockTypeField.value : "menu";
        toggleCollection(
            row.querySelectorAll(".board-block-menu-fields"),
            blockType === "menu"
        );
        toggleCollection(
            row.querySelectorAll(".board-block-body-field"),
            blockType === "text"
        );
        toggleCollection(
            row.querySelectorAll(".board-block-media-field"),
            blockType === "image" || blockType === "video"
        );
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
        var rows = container.querySelectorAll("[data-board-block]");
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

        container.setAttribute("data-next-index", String(rows.length));
    }

    function bindRow(row) {
        var blockTypeField = row.querySelector(".board-block-type");
        var removeButton = row.querySelector("[data-remove-board-block]");
        var moveUpButton = row.querySelector("[data-move-block-up]");
        var moveDownButton = row.querySelector("[data-move-block-down]");

        if (blockTypeField) {
            blockTypeField.addEventListener("change", function () {
                updateRowState(row);
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

        updateRowState(row);
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
        refreshRows();
    });

    Array.prototype.forEach.call(
        container.querySelectorAll("[data-board-block]"),
        function (row) {
            bindRow(row);
        }
    );
    refreshRows();
}());
