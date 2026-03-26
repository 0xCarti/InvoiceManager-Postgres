(function () {
    "use strict";

    function ready(fn) {
        if (document.readyState !== "loading") {
            fn();
        } else {
            document.addEventListener("DOMContentLoaded", fn);
        }
    }

    function toggleStatus(statusEl, message, isError) {
        if (!statusEl) {
            return;
        }
        statusEl.textContent = message;
        statusEl.classList.remove("d-none", "text-danger", "text-success");
        statusEl.classList.add(isError ? "text-danger" : "text-success");
    }

    function clearStatus(statusEl) {
        if (!statusEl) {
            return;
        }
        statusEl.textContent = "";
        statusEl.classList.add("d-none");
        statusEl.classList.remove("text-danger", "text-success");
    }

    ready(function () {
        var productSelect = document.getElementById("product_ids");

        var menuForm = document.getElementById("menu-form");
        var endpoint = null;
        if (menuForm && menuForm.dataset.productsEndpoint) {
            endpoint = menuForm.dataset.productsEndpoint;
        }

        var searchInput = document.getElementById("product-search");
        var clearSearchButton = document.getElementById("product-search-clear");

        function applyProductFilter() {
            if (!productSelect || !searchInput) {
                return;
            }
            var term = searchInput.value.trim().toLowerCase();
            Array.prototype.forEach.call(productSelect.options, function (option) {
                if (!term) {
                    option.hidden = false;
                    return;
                }
                var matches = option.text.toLowerCase().indexOf(term) !== -1;
                option.hidden = !matches;
            });
        }

        if (productSelect && searchInput) {
            searchInput.addEventListener("input", applyProductFilter);
        }

        if (productSelect && clearSearchButton) {
            clearSearchButton.addEventListener("click", function () {
                if (!searchInput) {
                    return;
                }
                searchInput.value = "";
                applyProductFilter();
                searchInput.focus();
            });
        }

        var copySelect = document.getElementById("copy-menu-select");
        var copyButton = document.getElementById("copy-menu-button");
        var statusEl = document.getElementById("copy-menu-status");

        if (!endpoint && copySelect && copySelect.dataset.productsEndpoint) {
            endpoint = copySelect.dataset.productsEndpoint;
        }

        function setSelectedProducts(productIds) {
            if (!productSelect) {
                return;
            }
            var idSet = new Set(productIds.map(function (id) {
                return String(id);
            }));
            Array.prototype.forEach.call(productSelect.options, function (option) {
                option.selected = idSet.has(option.value);
            });
            applyProductFilter();
        }

        if (productSelect && copySelect && copyButton && endpoint) {
            copyButton.addEventListener("click", function () {
                clearStatus(statusEl);
                var selectedMenuId = copySelect.value;
                if (!selectedMenuId) {
                    toggleStatus(statusEl, "Please choose a menu to copy from.", true);
                    return;
                }

                copyButton.disabled = true;
                var originalText = copyButton.textContent;
                copyButton.textContent = "Copying…";

                var url = endpoint + (endpoint.indexOf("?") === -1 ? "?" : "&") + "menu_id=" + encodeURIComponent(selectedMenuId);

                fetch(url, {
                    headers: {
                        Accept: "application/json"
                    }
                })
                    .then(function (response) {
                        if (!response.ok) {
                            throw new Error("Unable to load menu products");
                        }
                        return response.json();
                    })
                    .then(function (data) {
                        if (!data || !Array.isArray(data.product_ids)) {
                            throw new Error("Unexpected response from server");
                        }
                        setSelectedProducts(data.product_ids);
                        toggleStatus(statusEl, "Copied products from " + (data.name || "selected menu") + ".", false);
                    })
                    .catch(function (error) {
                        console.error(error);
                        toggleStatus(statusEl, error.message || "Unable to copy products.", true);
                    })
                    .finally(function () {
                        copyButton.disabled = false;
                        copyButton.textContent = originalText;
                    });
            });
        }

        var quickProductForm = document.getElementById("quick-product-form");
        var quickProductErrors = document.getElementById("quick-product-errors");
        var quickProductFeedback = document.getElementById("quick-product-feedback");
        var quickProductModalEl = document.getElementById("quickProductModal");
        var quickRecipeContainer = document.getElementById("quick-recipe-items");
        var quickRecipeInitialMarkup = quickRecipeContainer
            ? quickRecipeContainer.innerHTML
            : "";
        var quickRecipeInitialNextIndex = quickRecipeContainer
            ? quickRecipeContainer.dataset.nextIndex || "0"
            : "0";
        var quickRecipeCountableLabel = quickRecipeContainer
            ? quickRecipeContainer.dataset.countableLabel || "Countable"
            : "Countable";
        var quickItemIndex = 0;

        function showQuickProductFeedback(message, isError) {
            if (!quickProductFeedback) {
                return;
            }
            quickProductFeedback.textContent = message;
            quickProductFeedback.classList.remove("d-none", "text-danger", "text-success");
            quickProductFeedback.classList.add(isError ? "text-danger" : "text-success");
        }

        function clearQuickProductFeedback() {
            if (!quickProductFeedback) {
                return;
            }
            quickProductFeedback.textContent = "";
            quickProductFeedback.classList.add("d-none");
            quickProductFeedback.classList.remove("text-danger", "text-success");
        }

        function displayQuickProductErrors(errors) {
            if (!quickProductErrors) {
                return;
            }
            if (!errors) {
                quickProductErrors.innerHTML = "";
                quickProductErrors.classList.add("d-none");
                return;
            }
            var messages = [];
            Object.keys(errors).forEach(function (field) {
                var fieldErrors = errors[field];
                if (Array.isArray(fieldErrors)) {
                    fieldErrors.forEach(function (message) {
                        messages.push(message);
                    });
                }
            });
            if (!messages.length) {
                quickProductErrors.innerHTML = "";
                quickProductErrors.classList.add("d-none");
                return;
            }
            var list = document.createElement("ul");
            list.classList.add("mb-0");
            messages.forEach(function (message) {
                var item = document.createElement("li");
                item.textContent = message;
                list.appendChild(item);
            });
            quickProductErrors.innerHTML = "";
            quickProductErrors.appendChild(list);
            quickProductErrors.classList.remove("d-none");
        }

        function sortProductOptions() {
            if (!productSelect) {
                return;
            }
            var options = Array.prototype.slice.call(productSelect.options);
            options.sort(function (a, b) {
                return a.text.localeCompare(b.text);
            });
            productSelect.innerHTML = "";
            options.forEach(function (option) {
                productSelect.appendChild(option);
            });
        }

        function fetchQuickRecipeUnits(row, itemId, selected) {
            if (!row) {
                return;
            }
            var unitSelect = row.querySelector(".unit-select");
            if (!itemId) {
                if (unitSelect) {
                    unitSelect.innerHTML = "";
                }
                return;
            }
            fetch("/items/" + encodeURIComponent(itemId) + "/units")
                .then(function (response) {
                    if (!response.ok) {
                        throw new Error("Unable to load units");
                    }
                    return response.json();
                })
                .then(function (data) {
                    if (!unitSelect) {
                        return;
                    }
                    var options = "";
                    if (data && data.base_unit) {
                        options +=
                            '<option value="">' +
                            data.base_unit +
                            "</option>";
                    }
                    if (data && Array.isArray(data.units)) {
                        data.units.forEach(function (unit) {
                            options +=
                                '<option value="' +
                                unit.id +
                                '\">' +
                                unit.name +
                                " of " +
                                unit.factor +
                                " " +
                                data.base_unit +
                                (unit.factor !== 1 ? "s" : "") +
                                "</option>";
                        });
                    }
                    unitSelect.innerHTML = options;
                    if (selected) {
                        unitSelect.value = selected;
                    }
                })
                .catch(function (error) {
                    console.error(error);
                });
        }

        function createQuickRecipeRow(index) {
            var row = document.createElement("div");
            row.classList.add(
                "row",
                "g-2",
                "align-items-center",
                "quick-item-row",
                "mb-2"
            );
            row.innerHTML =
                '<div class="col position-relative">' +
                '<input type="hidden" name="items-' +
                index +
                '-item" class="item-id">' +
                '<input type="text" class="form-control form-control-sm item-search" placeholder="Search item…" autocomplete="off">' +
                '<div class="list-group item-suggestions"></div>' +
                "</div>" +
                '<div class="col">' +
                '<select name="items-' +
                index +
                '-unit" class="form-select form-select-sm unit-select"></select>' +
                "</div>" +
                '<div class="col">' +
                '<input type="number" step="any" name="items-' +
                index +
                '-quantity" class="form-control form-control-sm" placeholder="Qty">' +
                "</div>" +
                '<div class="col-auto form-check d-flex align-items-center">' +
                '<input type="checkbox" name="items-' +
                index +
                '-countable" class="form-check-input" id="items-' +
                index +
                '-countable">' +
                '<label class="form-check-label ms-1" for="items-' +
                index +
                '-countable">' +
                quickRecipeCountableLabel +
                "</label>" +
                "</div>" +
                '<div class="col-auto">' +
                '<button type="button" class="btn btn-sm btn-outline-danger quick-remove-item">Remove</button>' +
                "</div>";
            return row;
        }

        function addQuickRecipeRow() {
            if (!quickRecipeContainer) {
                return;
            }
            // Update the index from existing DOM nodes in case the dataset was
            // reset or rows were removed before this click.
            computeQuickItemIndex();
            var row = createQuickRecipeRow(quickItemIndex);
            quickRecipeContainer.appendChild(row);
            quickItemIndex += 1;
            quickRecipeContainer.dataset.nextIndex = String(quickItemIndex);
            var searchInput = row.querySelector(".item-search");
            if (searchInput) {
                searchInput.focus();
            }
        }

        function computeQuickItemIndex() {
            if (!quickRecipeContainer) {
                return;
            }
            var nextIndex = parseInt(
                quickRecipeContainer.dataset.nextIndex || "0",
                10
            );
            if (Number.isNaN(nextIndex)) {
                nextIndex = 0;
            }
            quickRecipeContainer
                .querySelectorAll(".quick-item-row .item-id")
                .forEach(function (input) {
                    if (!input.name) {
                        return;
                    }
                    var match = input.name.match(/items-(\d+)-item/);
                    if (match) {
                        var idx = parseInt(match[1], 10);
                        if (!Number.isNaN(idx) && idx >= nextIndex) {
                            nextIndex = idx + 1;
                        }
                    }
                });
            quickItemIndex = nextIndex;
            quickRecipeContainer.dataset.nextIndex = String(nextIndex);
        }

        function setupQuickRecipeRows() {
            if (!quickRecipeContainer) {
                return;
            }
            quickRecipeContainer
                .querySelectorAll(".quick-item-row")
                .forEach(function (row) {
                    var hiddenInput = row.querySelector(".item-id");
                    var unitSelect = row.querySelector(".unit-select");
                    var selected = unitSelect ? unitSelect.dataset.selected : null;
                    if (hiddenInput && hiddenInput.value) {
                        fetchQuickRecipeUnits(row, hiddenInput.value, selected);
                    } else if (unitSelect) {
                        unitSelect.innerHTML = "";
                    }
                });
            computeQuickItemIndex();
        }

        function resetQuickProductForm() {
            if (quickProductForm) {
                quickProductForm.reset();
            }
            if (quickRecipeContainer) {
                quickRecipeContainer.innerHTML = quickRecipeInitialMarkup;
                quickRecipeContainer.dataset.nextIndex = quickRecipeInitialNextIndex;
                setupQuickRecipeRows();
            }
        }

        if (quickRecipeContainer) {
            setupQuickRecipeRows();

            document.addEventListener("click", function (event) {
                var target = event.target;
                if (!target) {
                    return;
                }
                if (target.id === "quick-add-recipe-item") {
                    event.preventDefault();
                    addQuickRecipeRow();
                    return;
                }
                if (typeof target.closest === "function") {
                    var button = target.closest("#quick-add-recipe-item");
                    if (button) {
                        event.preventDefault();
                        addQuickRecipeRow();
                    }
                }
            });

            quickRecipeContainer.addEventListener("click", function (event) {
                var target = event.target;
                if (!target) {
                    return;
                }
                if (target.classList.contains("quick-remove-item")) {
                    event.preventDefault();
                    var row = target.closest(".quick-item-row");
                    if (row) {
                        row.remove();
                    }
                    return;
                }
                if (target.classList.contains("item-suggestion")) {
                    event.preventDefault();
                    var suggestionRow = target.closest(".quick-item-row");
                    if (!suggestionRow) {
                        return;
                    }
                    var hidden = suggestionRow.querySelector(".item-id");
                    if (hidden) {
                        hidden.value = target.dataset.id || "";
                    }
                    var searchField = suggestionRow.querySelector(".item-search");
                    if (searchField) {
                        searchField.value = target.textContent || "";
                    }
                    var suggestions = suggestionRow.querySelector(
                        ".item-suggestions"
                    );
                    if (suggestions) {
                        suggestions.innerHTML = "";
                    }
                    fetchQuickRecipeUnits(
                        suggestionRow,
                        target.dataset.id || null,
                        null
                    );
                }
            });

            quickRecipeContainer.addEventListener("input", function (event) {
                var target = event.target;
                if (!target || !target.classList.contains("item-search")) {
                    return;
                }
                var row = target.closest(".quick-item-row");
                if (!row) {
                    return;
                }
                var query = target.value.trim();
                var hiddenField = row.querySelector(".item-id");
                var suggestionList = row.querySelector(".item-suggestions");
                if (!query) {
                    if (hiddenField) {
                        hiddenField.value = "";
                    }
                    if (suggestionList) {
                        suggestionList.innerHTML = "";
                    }
                    fetchQuickRecipeUnits(row, null, null);
                    return;
                }
                fetch("/items/search?term=" + encodeURIComponent(query))
                    .then(function (response) {
                        if (!response.ok) {
                            throw new Error("Unable to search items");
                        }
                        return response.json();
                    })
                    .then(function (data) {
                        if (!suggestionList) {
                            return;
                        }
                        if (!Array.isArray(data)) {
                            suggestionList.innerHTML = "";
                            return;
                        }
                        suggestionList.innerHTML = data
                            .map(function (item) {
                                return (
                                    '<a href="#" class="list-group-item list-group-item-action item-suggestion" data-id="' +
                                    item.id +
                                    '\">' +
                                    item.name +
                                    "</a>"
                                );
                            })
                            .join("");
                    })
                    .catch(function (error) {
                        console.error(error);
                        if (suggestionList) {
                            suggestionList.innerHTML = "";
                        }
                    });
            });
        }

        if (quickProductForm) {
            quickProductForm.addEventListener("submit", function (event) {
                event.preventDefault();
                displayQuickProductErrors(null);
                clearQuickProductFeedback();
                var submitButton = quickProductForm.querySelector("button[type='submit'], input[type='submit']");
                if (submitButton) {
                    submitButton.disabled = true;
                    if (submitButton.tagName === "BUTTON") {
                        submitButton.dataset.originalLabel = submitButton.textContent;
                        submitButton.textContent = "Saving…";
                    } else {
                        submitButton.dataset.originalLabel = submitButton.value;
                        submitButton.value = "Saving…";
                    }
                }
                var formData = new FormData(quickProductForm);
                fetch(quickProductForm.getAttribute("action"), {
                    method: "POST",
                    headers: {
                        Accept: "application/json"
                    },
                    body: formData
                })
                    .then(function (response) {
                        if (response.ok) {
                            return response.json();
                        }
                        return response.json().then(function (data) {
                            throw { response: data };
                        }).catch(function () {
                            throw new Error("Unable to create product.");
                        });
                    })
                    .then(function (data) {
                        if (!data || !data.product) {
                            throw new Error("Unexpected response from server.");
                        }
                        var product = data.product;
                        var existing = null;
                        if (productSelect) {
                            Array.prototype.forEach.call(
                                productSelect.options,
                                function (option) {
                                    if (option.value === String(product.id)) {
                                        existing = option;
                                    }
                                }
                            );
                            if (!existing) {
                                var newOption = new Option(
                                    product.name,
                                    product.id,
                                    true,
                                    true
                                );
                                productSelect.appendChild(newOption);
                                sortProductOptions();
                            } else {
                                existing.selected = true;
                            }
                            applyProductFilter();
                        }
                        showQuickProductFeedback(
                            "Created " + product.name + " and added it to this menu.",
                            false
                        );
                        if (quickProductModalEl && window.bootstrap) {
                            var modalInstance = window.bootstrap.Modal.getInstance(quickProductModalEl);
                            if (!modalInstance) {
                                modalInstance = new window.bootstrap.Modal(quickProductModalEl);
                            }
                            modalInstance.hide();
                        }
                        resetQuickProductForm();
                    })
                    .catch(function (error) {
                        if (error && error.response && error.response.errors) {
                            displayQuickProductErrors(error.response.errors);
                        } else {
                            displayQuickProductErrors({ __all__: [error.message || "Unable to create product."] });
                        }
                    })
                    .finally(function () {
                        if (submitButton) {
                            submitButton.disabled = false;
                            var label = submitButton.dataset.originalLabel || "Create Product";
                            if (submitButton.tagName === "BUTTON") {
                                submitButton.textContent = label;
                            } else {
                                submitButton.value = label;
                            }
                            delete submitButton.dataset.originalLabel;
                        }
                    });
            });

            if (quickProductModalEl && window.bootstrap) {
                quickProductModalEl.addEventListener("hidden.bs.modal", function () {
                    displayQuickProductErrors(null);
                    resetQuickProductForm();
                });
            }
        }
    });
})();
