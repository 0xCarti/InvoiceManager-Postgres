(function (window, document) {
    "use strict";

    const SELECTOR = [
        "select[data-item-search=\"1\"]",
        "select.item-select",
        "select[data-role=\"alias-item-select\"]",
        "select[data-countable-select]"
    ].join(", ");

    function select2Available() {
        return Boolean(window.jQuery && window.jQuery.fn && window.jQuery.fn.select2);
    }

    function buildConfig(select) {
        const $ = window.jQuery;
        const isMultiple = select.multiple || select.dataset.itemSearchMultiple === "1";
        const hasBlankOption = Array.from(select.options || []).some(
            (option) => option.value === ""
        );
        const placeholder =
            select.dataset.itemSearchPlaceholder ||
            (isMultiple ? "Search items..." : "Search items...");
        const modal = select.closest(".modal");
        const config = {
            width: "100%",
            placeholder: placeholder,
            minimumResultsForSearch: 0
        };

        if (isMultiple) {
            config.closeOnSelect = false;
        } else if (hasBlankOption && !select.required) {
            config.allowClear = true;
        }

        if (modal) {
            config.dropdownParent = $(modal);
        }

        return config;
    }

    function initSelect(select) {
        if (!(select instanceof window.HTMLSelectElement)) {
            return;
        }
        if (!select.matches(SELECTOR)) {
            return;
        }
        if (select.dataset.itemSearchReady === "1") {
            return;
        }
        if (!select2Available()) {
            return;
        }

        const $select = window.jQuery(select);
        if (!$select.data("select2")) {
            $select.select2(buildConfig(select));
        }
        select.dataset.itemSearchReady = "1";
    }

    function initWithin(root) {
        if (!root) {
            return;
        }
        if (root instanceof window.HTMLSelectElement) {
            initSelect(root);
            return;
        }
        if (root.querySelectorAll) {
            root.querySelectorAll(SELECTOR).forEach(initSelect);
        }
    }

    function observe() {
        if (window.__itemSearchSelectObserver || !document.body) {
            return;
        }
        const observer = new window.MutationObserver((mutations) => {
            mutations.forEach((mutation) => {
                mutation.addedNodes.forEach((node) => {
                    if (node instanceof window.HTMLElement) {
                        initWithin(node);
                    }
                });
            });
        });
        observer.observe(document.body, {
            childList: true,
            subtree: true
        });
        window.__itemSearchSelectObserver = observer;
    }

    function boot() {
        initWithin(document);
        observe();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

    document.addEventListener("shown.bs.modal", (event) => {
        initWithin(event.target);
    });

    window.initItemSearchSelects = initWithin;
})(window, document);
