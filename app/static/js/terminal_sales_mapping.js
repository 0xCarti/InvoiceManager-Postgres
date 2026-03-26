(function () {
    "use strict";

    function initTerminalProductMappings() {
        var containers = document.querySelectorAll("[data-terminal-product-mapping]");
        if (!containers.length) {
            return;
        }

        var datalist = document.getElementById("terminal-product-options");
        var datalistOptions = datalist ? Array.prototype.slice.call(datalist.options) : [];
        var optionByValue = Object.create(null);
        var optionByLowerValue = Object.create(null);
        var optionById = Object.create(null);

        function registerOption(option) {
            if (!option) {
                return;
            }
            var value = option.value || "";
            var label = option.label || "";
            var id = option.dataset ? option.dataset.id : option.getAttribute("data-id");
            if (value) {
                optionByValue[value] = option;
                optionByLowerValue[value.toLowerCase()] = option;
            }
            if (label) {
                optionByLowerValue[label.toLowerCase()] = option;
            }
            if (id) {
                optionById[id] = option;
            }
        }

        datalistOptions.forEach(registerOption);

        function resolveOption(rawValue) {
            if (!rawValue) {
                return null;
            }
            var option = optionByValue[rawValue];
            if (option) {
                return option;
            }
            var lower = rawValue.toLowerCase();
            option = optionByLowerValue[lower];
            if (option) {
                return option;
            }
            if (!datalist) {
                return null;
            }
            for (var i = 0; i < datalist.options.length; i += 1) {
                var candidate = datalist.options[i];
                if ((candidate.value || "").toLowerCase() === lower) {
                    return candidate;
                }
                if ((candidate.label || "").toLowerCase() === lower) {
                    return candidate;
                }
            }
            return null;
        }

        function setStatusMessage(target, message, tone) {
            if (!target) {
                return;
            }
            target.textContent = message || "";
            target.classList.remove("text-danger", "text-success", "text-muted");
            if (!message) {
                return;
            }
            switch (tone) {
                case "danger":
                    target.classList.add("text-danger");
                    break;
                case "success":
                    target.classList.add("text-success");
                    break;
                default:
                    target.classList.add("text-muted");
                    break;
            }
        }

        var createdIdContainer = document.querySelector("[data-role='created-product-container']");
        var modalElement = document.getElementById("terminalCreateProductModal");
        var modalForm = modalElement ? modalElement.querySelector("form") : null;
        var modalInstance =
            modalElement && modalForm && typeof bootstrap !== "undefined"
                ? bootstrap.Modal.getOrCreateInstance(modalElement)
                : null;
        var activeMapping = null;
        var createdIds = new Set();

        function collectCreatedIdsFromContainers() {
            var ids = new Set();
            containers.forEach(function (container) {
                if (!container || !container.dataset) {
                    return;
                }
                var raw = container.dataset.createdProductId;
                if (!raw) {
                    return;
                }
                var parsed = parseInt(raw, 10);
                if (!Number.isNaN(parsed)) {
                    ids.add(parsed);
                }
            });
            return ids;
        }

        function refreshCreatedIdInputs() {
            if (!createdIdContainer) {
                return;
            }
            createdIdContainer.innerHTML = "";
            createdIds.forEach(function (id) {
                var input = document.createElement("input");
                input.type = "hidden";
                input.name = "created_product_ids";
                input.value = String(id);
                createdIdContainer.appendChild(input);
            });
        }

        function syncCreatedIdState() {
            createdIds = collectCreatedIdsFromContainers();
            refreshCreatedIdInputs();
        }

        function ensureOptionForProduct(productId, productName) {
            if (!Number.isFinite(productId)) {
                return null;
            }
            var idString = String(productId);
            var displayName = productName ? String(productName) : "Product " + idString;
            var displayValue = displayName + " (ID: " + idString + ")";
            var option = optionById[idString];
            if (option) {
                option.value = displayValue;
                option.label = displayName;
            } else if (datalist) {
                option = document.createElement("option");
                option.value = displayValue;
                option.label = displayName;
                option.setAttribute("data-id", idString);
                datalist.appendChild(option);
            }
            if (option) {
                if (option.dataset) {
                    option.dataset.id = idString;
                }
                registerOption(option);
            }
            return option;
        }

        function resetModalForm() {
            if (!modalForm) {
                return;
            }
            modalForm.reset();
            var itemList = modalForm.querySelector("[data-role='item-list']");
            if (itemList) {
                itemList.innerHTML = "";
            }
        }

        function openModalForContainer(container) {
            if (!modalInstance || !modalForm) {
                return false;
            }
            resetModalForm();
            activeMapping = container;
            var sourceName = container.getAttribute("data-product-name") || "";
            var sourcePrice = container.getAttribute("data-product-price") || "";
            var nameInput = modalForm.querySelector("[name='name']");
            if (nameInput) {
                nameInput.value = sourceName;
            }
            var priceInput = modalForm.querySelector("[name='price']");
            if (priceInput) {
                priceInput.value = sourcePrice;
            }
            var yieldInput = modalForm.querySelector("[name='recipe_yield_quantity']");
            if (yieldInput && !yieldInput.value) {
                yieldInput.value = 1;
            }
            var salesGlSelect = modalForm.querySelector("[name='sales_gl_code']");
            if (salesGlSelect) {
                salesGlSelect.value = salesGlSelect.options.length ? salesGlSelect.options[0].value : "";
            }
            modalInstance.show();
            if (nameInput) {
                setTimeout(function () {
                    nameInput.focus();
                    nameInput.select();
                }, 150);
            }
            return true;
        }

        function handleProductCreated(productData) {
            if (!activeMapping) {
                return;
            }
            var productId = productData && productData.id ? parseInt(productData.id, 10) : NaN;
            var productName = productData && productData.name ? String(productData.name) : "";
            if (!productName) {
                productName = activeMapping.getAttribute("data-product-name") || "";
            }
            if (!Number.isFinite(productId)) {
                window.alert("Unable to determine the new product ID.");
                activeMapping = null;
                return;
            }
            createdIds.add(productId);
            var option = ensureOptionForProduct(productId, productName);
            var helpers = activeMapping._tsm || {};
            if (typeof helpers.linkToOption === "function") {
                helpers.linkToOption(option, true);
            } else {
                var hiddenInput = helpers.hiddenInput || activeMapping.querySelector("[data-role='product-value']");
                if (hiddenInput) {
                    hiddenInput.value = String(productId);
                }
                var searchInput = helpers.searchInput || activeMapping.querySelector("[data-role='product-search-input']");
                if (searchInput) {
                    searchInput.value = option ? option.value : productName;
                }
                if (helpers.statusMessage) {
                    setStatusMessage(
                        helpers.statusMessage,
                        productName ? "Linked to " + productName : "Linked to new product",
                        "success"
                    );
                }
            }
            activeMapping.dataset.createdProductId = String(productId);
            syncCreatedIdState();
            activeMapping = null;
        }

        syncCreatedIdState();

        containers.forEach(function (container) {
            var searchInput = container.querySelector("[data-role='product-search-input']");
            var hiddenInput = container.querySelector("[data-role='product-value']");
            if (!hiddenInput) {
                return;
            }
            var statusMessage = container.querySelector("[data-role='selection-status']");
            var errorMessage = container.querySelector("[data-role='selection-error']");
            var skipButton = container.querySelector("[data-action='skip']");
            var createButton = container.querySelector("[data-action='create']");
            var clearButton = container.querySelector("[data-action='clear']");
            var skipValue = container.getAttribute("data-skip-value") || "";
            var createValue = container.getAttribute("data-create-value") || "";

            function hideError() {
                if (errorMessage) {
                    errorMessage.classList.add("d-none");
                }
            }

            function showError() {
                if (errorMessage) {
                    errorMessage.classList.remove("d-none");
                }
            }

            function setActiveButton(activeButton) {
                [skipButton, createButton].forEach(function (button) {
                    if (!button) {
                        return;
                    }
                    if (button === activeButton) {
                        button.classList.add("active");
                    } else {
                        button.classList.remove("active");
                    }
                });
            }

            function linkToOption(option, forceCreated) {
                if (!option || !hiddenInput) {
                    return;
                }
                var optionId = option.dataset ? option.dataset.id : option.getAttribute("data-id");
                if (!optionId) {
                    return;
                }
                hiddenInput.value = optionId;
                setActiveButton(null);
                hideError();
                var display = option.value || option.label || option.textContent || "";
                if (searchInput && display && searchInput.value !== display) {
                    searchInput.value = display;
                }
                var parsedId = parseInt(optionId, 10);
                if (!Number.isNaN(parsedId)) {
                    if (forceCreated || createdIds.has(parsedId)) {
                        container.dataset.createdProductId = String(parsedId);
                    } else {
                        delete container.dataset.createdProductId;
                    }
                    syncCreatedIdState();
                }
                setStatusMessage(
                    statusMessage,
                    display ? "Linked to " + display : "",
                    forceCreated ? "success" : "muted"
                );
            }

            function resetSelectionState() {
                setActiveButton(null);
                hideError();
                delete container.dataset.createdProductId;
                syncCreatedIdState();
                if (!searchInput || !searchInput.value.trim()) {
                    setStatusMessage(statusMessage, "", null);
                }
            }

            function initializeState() {
                hideError();
                var value = hiddenInput.value || "";
                if (!value) {
                    resetSelectionState();
                    return;
                }
                if (value === skipValue) {
                    if (searchInput) {
                        searchInput.value = "";
                    }
                    delete container.dataset.createdProductId;
                    syncCreatedIdState();
                    setActiveButton(skipButton);
                    setStatusMessage(statusMessage, "This terminal sale product will be skipped.", "muted");
                    return;
                }
                if (value === createValue) {
                    if (searchInput) {
                        searchInput.value = "";
                    }
                    delete container.dataset.createdProductId;
                    syncCreatedIdState();
                    setActiveButton(createButton);
                    setStatusMessage(
                        statusMessage,
                        "Create the product before continuing.",
                        "danger"
                    );
                    return;
                }
                var option = optionById[value];
                if (option) {
                    linkToOption(option, false);
                } else if (searchInput && searchInput.value) {
                    var inferred = resolveOption(searchInput.value.trim());
                    if (inferred) {
                        linkToOption(inferred, false);
                    } else {
                        delete container.dataset.createdProductId;
                        syncCreatedIdState();
                        setStatusMessage(statusMessage, "", null);
                    }
                }
            }

            if (clearButton) {
                clearButton.addEventListener("click", function () {
                    if (searchInput) {
                        searchInput.value = "";
                        searchInput.focus();
                    }
                    hiddenInput.value = "";
                    delete container.dataset.createdProductId;
                    syncCreatedIdState();
                    setActiveButton(null);
                    hideError();
                    setStatusMessage(statusMessage, "", null);
                });
            }

            if (skipButton) {
                skipButton.addEventListener("click", function () {
                    hiddenInput.value = skipValue;
                    if (searchInput) {
                        searchInput.value = "";
                    }
                    delete container.dataset.createdProductId;
                    syncCreatedIdState();
                    setActiveButton(skipButton);
                    hideError();
                    setStatusMessage(statusMessage, "This terminal sale product will be skipped.", "muted");
                });
            }

            if (createButton) {
                createButton.addEventListener("click", function (event) {
                    if (openModalForContainer(container)) {
                        event.preventDefault();
                        return;
                    }
                    hiddenInput.value = createValue;
                    if (searchInput) {
                        searchInput.value = "";
                    }
                    delete container.dataset.createdProductId;
                    syncCreatedIdState();
                    setActiveButton(createButton);
                    hideError();
                    setStatusMessage(
                        statusMessage,
                        "A new product will be created from this sale item.",
                        "muted"
                    );
                });
            }

            if (searchInput) {
                searchInput.addEventListener("input", function () {
                    hiddenInput.value = "";
                    delete container.dataset.createdProductId;
                    syncCreatedIdState();
                    setActiveButton(null);
                    hideError();
                    if (searchInput.value.trim()) {
                        setStatusMessage(statusMessage, "Select a product from the list to confirm the match.", "muted");
                    } else {
                        setStatusMessage(statusMessage, "", null);
                    }
                });

                searchInput.addEventListener("change", function () {
                    var raw = searchInput.value.trim();
                    if (!raw) {
                        hiddenInput.value = "";
                        resetSelectionState();
                        return;
                    }
                    var option = resolveOption(raw);
                    if (option && (option.dataset ? option.dataset.id : option.getAttribute("data-id"))) {
                        linkToOption(option, false);
                    } else {
                        hiddenInput.value = "";
                        delete container.dataset.createdProductId;
                        syncCreatedIdState();
                        showError();
                        setStatusMessage(statusMessage, "", null);
                    }
                });
            }

            initializeState();

            container._tsm = {
                linkToOption: linkToOption,
                setActiveButton: setActiveButton,
                hideError: hideError,
                statusMessage: statusMessage,
                hiddenInput: hiddenInput,
                searchInput: searchInput,
            };
        });

        if (modalElement && modalForm && modalInstance) {
            modalElement.addEventListener("hidden.bs.modal", function () {
                resetModalForm();
                activeMapping = null;
            });

            modalForm.addEventListener("submit", function (event) {
                event.preventDefault();
                if (!activeMapping) {
                    window.alert("Select a product mapping before creating a product.");
                    return;
                }
                var action = modalForm.getAttribute("action") || modalForm.action || "";
                if (!action) {
                    window.alert("Unable to submit the product form.");
                    return;
                }
                var submitButton = modalForm.querySelector("[type='submit']");
                if (submitButton) {
                    submitButton.disabled = true;
                }
                var formData = new FormData(modalForm);
                fetch(action, {
                    method: "POST",
                    body: formData,
                    credentials: "same-origin",
                })
                    .then(function (response) {
                        return response
                            .json()
                            .catch(function () {
                                return { success: false };
                            })
                            .then(function (data) {
                                if (!response.ok || !data || !data.success) {
                                    var message = data && data.errors ? JSON.stringify(data.errors) : "Unable to create product.";
                                    throw new Error(message);
                                }
                                return data;
                            });
                    })
                    .then(function (data) {
                        modalInstance.hide();
                        handleProductCreated(data.product || {});
                    })
                    .catch(function (error) {
                        var message = error && error.message ? error.message : "Unable to create product.";
                        window.alert(message);
                    })
                    .finally(function () {
                        if (submitButton) {
                            submitButton.disabled = false;
                        }
                    });
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initTerminalProductMappings);
    } else {
        initTerminalProductMappings();
    }
})();

