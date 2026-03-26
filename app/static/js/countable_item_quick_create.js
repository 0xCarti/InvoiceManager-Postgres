(function () {
    "use strict";

    function parseNumericInput(input) {
        if (!input) {
            return NaN;
        }
        if (
            typeof window !== "undefined" &&
            window.NumericInput &&
            typeof window.NumericInput.parseValue === "function"
        ) {
            const parsed = window.NumericInput.parseValue(input);
            if (Number.isFinite(parsed)) {
                return parsed;
            }
        }
        const parsed = Number(input.value);
        return Number.isFinite(parsed) ? parsed : NaN;
    }

    document.addEventListener("DOMContentLoaded", () => {
        const selectionContainer = document.querySelector(
            "[data-countable-selection]"
        );
        if (!selectionContainer) {
            return;
        }

        const modalElement = document.getElementById("newItemModal");
        const saveButton = document.getElementById("save-new-item");
        const addUnitButton = document.getElementById("add-new-item-unit");
        const baseUnitSelect = document.getElementById("new-item-base-unit");
        const unitsContainer = document.getElementById("new-item-units");
        const nameInput = document.getElementById("new-item-name");
        const glCodeSelect = document.getElementById("new-item-gl-code");
        const csrfTokenInput = document.querySelector("input[name=\"csrf_token\"]");

        const triggerButtons = Array.from(
            selectionContainer.querySelectorAll(
                "[data-countable-action=\"quick-add-item\"]"
            )
        );
        const selectElements = Array.from(
            selectionContainer.querySelectorAll("[data-countable-select]")
        );

        if (
            !modalElement ||
            !saveButton ||
            !addUnitButton ||
            !unitsContainer ||
            !triggerButtons.length ||
            !selectElements.length
        ) {
            return;
        }

        const modalInstance =
            typeof bootstrap !== "undefined"
                ? bootstrap.Modal.getOrCreateInstance(modalElement)
                : null;

        let newItemUnitIndex = 0;
        let activeSelect = null;

        function ensureUnitDefaults() {
            if (!unitsContainer) {
                return;
            }
            const receivingChecked = unitsContainer.querySelector(
                ".new-item-unit-receiving:checked"
            );
            if (!receivingChecked) {
                const fallback = unitsContainer.querySelector(
                    '.new-item-unit-row[data-base="true"] .new-item-unit-receiving'
                );
                if (fallback) {
                    fallback.checked = true;
                }
            }

            const transferChecked = unitsContainer.querySelector(
                ".new-item-unit-transfer:checked"
            );
            if (!transferChecked) {
                const fallback = unitsContainer.querySelector(
                    '.new-item-unit-row[data-base="true"] .new-item-unit-transfer'
                );
                if (fallback) {
                    fallback.checked = true;
                }
            }
        }

        function syncBaseUnitRow() {
            if (!unitsContainer || !baseUnitSelect) {
                return;
            }
            const baseRow = unitsContainer.querySelector(
                '.new-item-unit-row[data-base="true"]'
            );
            if (!baseRow) {
                return;
            }
            const nameField = baseRow.querySelector(".new-item-unit-name");
            if (nameField) {
                nameField.value = baseUnitSelect.value || "";
            }
        }

        function createUnitRow(options = {}) {
            const row = document.createElement("div");
            row.classList.add(
                "row",
                "g-2",
                "new-item-unit-row",
                "border",
                "rounded",
                "p-2"
            );
            row.dataset.base = options.isBase ? "true" : "false";
            const index = newItemUnitIndex++;

            const nameCol = document.createElement("div");
            nameCol.classList.add("col-12", "col-md-4");
            const nameLabel = document.createElement("label");
            nameLabel.classList.add("form-label");
            const nameId = `new-item-unit-${index}-name`;
            nameLabel.setAttribute("for", nameId);
            nameLabel.textContent = options.isBase ? "Base Unit" : "Unit Name";
            const nameField = document.createElement("input");
            nameField.type = "text";
            nameField.classList.add("form-control", "new-item-unit-name");
            nameField.id = nameId;
            nameField.value = options.name || "";
            if (options.isBase) {
                nameField.readOnly = true;
            } else {
                nameField.placeholder = "e.g. Case";
            }
            nameCol.append(nameLabel, nameField);
            row.appendChild(nameCol);

            const factorCol = document.createElement("div");
            factorCol.classList.add("col-12", "col-md-3");
            const factorLabel = document.createElement("label");
            factorLabel.classList.add("form-label");
            const factorId = `new-item-unit-${index}-factor`;
            factorLabel.setAttribute("for", factorId);
            factorLabel.textContent = "Ratio to Base Unit";
            const factorField = document.createElement("input");
            factorField.type = "number";
            factorField.classList.add("form-control", "new-item-unit-factor");
            factorField.id = factorId;
            factorField.step = "any";
            factorField.min = "0";
            const factorValue =
                options.factor !== undefined && options.factor !== null
                    ? options.factor
                    : 1;
            factorField.value = options.isBase ? 1 : factorValue;
            if (options.isBase) {
                factorField.readOnly = true;
            }
            factorCol.append(factorLabel, factorField);
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
            const receivingField = document.createElement("input");
            receivingField.type = "radio";
            receivingField.classList.add(
                "form-check-input",
                "new-item-unit-receiving"
            );
            receivingField.name = "new-item-receiving-default";
            receivingField.id = receivingId;
            if (options.receivingDefault) {
                receivingField.checked = true;
            }
            const receivingLabel = document.createElement("label");
            receivingLabel.classList.add("form-check-label");
            receivingLabel.setAttribute("for", receivingId);
            receivingLabel.textContent = "Receiving Default";
            receivingWrapper.append(receivingField, receivingLabel);
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
            const transferField = document.createElement("input");
            transferField.type = "radio";
            transferField.classList.add(
                "form-check-input",
                "new-item-unit-transfer"
            );
            transferField.name = "new-item-transfer-default";
            transferField.id = transferId;
            if (options.transferDefault) {
                transferField.checked = true;
            }
            const transferLabel = document.createElement("label");
            transferLabel.classList.add("form-check-label");
            transferLabel.setAttribute("for", transferId);
            transferLabel.textContent = "Transfer Default";
            transferWrapper.append(transferField, transferLabel);
            transferCol.appendChild(transferWrapper);
            row.appendChild(transferCol);

            if (!options.isBase) {
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

        function appendUnitRow(options = {}) {
            if (!unitsContainer) {
                return null;
            }
            const row = createUnitRow(options);
            unitsContainer.appendChild(row);
            ensureUnitDefaults();
            return row;
        }

        function resetUnitRows() {
            if (!unitsContainer) {
                return;
            }
            unitsContainer.innerHTML = "";
            newItemUnitIndex = 0;
            const baseName = baseUnitSelect ? baseUnitSelect.value || "" : "";
            appendUnitRow({
                name: baseName,
                factor: 1,
                receivingDefault: true,
                transferDefault: true,
                isBase: true,
            });
            syncBaseUnitRow();
            ensureUnitDefaults();
        }

        resetUnitRows();

        if (baseUnitSelect) {
            baseUnitSelect.addEventListener("change", () => {
                syncBaseUnitRow();
            });
        }

        if (addUnitButton) {
            addUnitButton.addEventListener("click", () => {
                appendUnitRow({ receivingDefault: false, transferDefault: false });
            });
        }

        if (unitsContainer) {
            unitsContainer.addEventListener("click", (event) => {
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

        triggerButtons.forEach((button) => {
            button.addEventListener("click", () => {
                const targetId = button.getAttribute("data-target-select");
                const targetSelect = targetId
                    ? document.getElementById(targetId)
                    : null;
                activeSelect = targetSelect && selectElements.includes(targetSelect)
                    ? targetSelect
                    : selectElements[0] || null;

                if (nameInput) {
                    nameInput.value = "";
                }
                resetUnitRows();
                if (nameInput) {
                    nameInput.focus();
                }
                if (modalInstance) {
                    modalInstance.show();
                }
            });
        });

        function buildUnitsPayload(baseUnit) {
            const rows = Array.from(
                unitsContainer.querySelectorAll(".new-item-unit-row")
            );
            const unitsPayload = [];
            let hasInvalidUnits = false;
            let hasReceivingDefault = false;
            let hasTransferDefault = false;

            rows.forEach((row) => {
                const nameField = row.querySelector(".new-item-unit-name");
                const factorField = row.querySelector(".new-item-unit-factor");
                const receivingField = row.querySelector(
                    ".new-item-unit-receiving"
                );
                const transferField = row.querySelector(
                    ".new-item-unit-transfer"
                );

                const isBase = row.dataset.base === "true";
                let unitName = nameField ? nameField.value.trim() : "";
                let factorValue = parseNumericInput(factorField);

                if (isBase) {
                    unitName = baseUnit || "";
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

            return {
                unitsPayload,
                hasInvalidUnits,
                hasReceivingDefault,
                hasTransferDefault,
            };
        }

        if (saveButton) {
            saveButton.addEventListener("click", () => {
                const name = nameInput ? nameInput.value.trim() : "";
                const baseUnit = baseUnitSelect ? baseUnitSelect.value : "";
                const glCode = glCodeSelect ? glCodeSelect.value : null;
                const csrfToken = csrfTokenInput ? csrfTokenInput.value : null;

                const {
                    unitsPayload,
                    hasInvalidUnits,
                    hasReceivingDefault,
                    hasTransferDefault,
                } = buildUnitsPayload(baseUnit);

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
                if (baseUnitEntry) {
                    if (!hasReceivingDefault) {
                        baseUnitEntry.receiving_default = true;
                    }
                    if (!hasTransferDefault) {
                        baseUnitEntry.transfer_default = true;
                    }
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
                        const optionValue = String(data.id);
                        const baseUnitLabel = baseUnitSelect
                            ? baseUnitSelect.options[baseUnitSelect.selectedIndex]
                                ? baseUnitSelect.options[
                                      baseUnitSelect.selectedIndex
                                  ].text.trim()
                                : ""
                            : "";
                        const optionLabel = baseUnitLabel
                            ? `${data.name} (${baseUnitLabel})`
                            : data.name;

                        selectElements.forEach((select) => {
                            if (!select) {
                                return;
                            }
                            const existingOption = Array.from(select.options).find(
                                (opt) => opt.value === optionValue
                            );
                            if (existingOption) {
                                existingOption.textContent = optionLabel;
                                return;
                            }
                            const option = document.createElement("option");
                            option.value = optionValue;
                            option.textContent = optionLabel;
                            select.appendChild(option);
                        });

                        if (activeSelect) {
                            activeSelect.value = optionValue;
                            activeSelect.dispatchEvent(
                                new Event("change", { bubbles: true })
                            );
                        }

                        if (nameInput) {
                            nameInput.value = "";
                        }
                        resetUnitRows();
                        if (modalInstance) {
                            modalInstance.hide();
                        }
                    })
                    .catch(() => {
                        /* Silent failure keeps UI responsive */
                    });
            });
        }
    });
})();
