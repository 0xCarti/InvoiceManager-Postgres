(function (global) {
    const DEFAULT_OPTIONS = {
        dateFormat: 'Y-m-d',
        altInput: true,
        altFormat: 'F j, Y',
        allowInput: true,
    };

    const formListeners = new WeakSet();
    const modalListeners = new WeakSet();

    function toArray(value) {
        if (!value) {
            return [];
        }
        if (Array.isArray(value)) {
            return value;
        }
        return [value];
    }

    function getSelectors(fieldSelectors) {
        const selectors = toArray(fieldSelectors).filter(Boolean);
        if (selectors.length === 0) {
            selectors.push('[data-flatpickr]');
        }
        return selectors;
    }

    function collectInputs(selectors, root = document) {
        const elements = new Set();
        selectors.forEach((selector) => {
            root.querySelectorAll(selector).forEach((el) => elements.add(el));
        });
        return Array.from(elements);
    }

    function initialiseInputs(inputs) {
        inputs.forEach((input) => {
            if (!input) {
                return;
            }
            if (typeof flatpickr !== 'function') {
                return;
            }
            if (input._flatpickr) {
                input._flatpickr.destroy();
            }
            const options = { ...DEFAULT_OPTIONS };
            const dataDateFormat = input.getAttribute('data-date-format');
            const dataAltFormat = input.getAttribute('data-alt-format');
            if (dataDateFormat) {
                options.dateFormat = dataDateFormat;
            }
            if (dataAltFormat) {
                options.altFormat = dataAltFormat;
            }
            const instance = flatpickr(input, options);
            const form = input.form;
            if (form && !formListeners.has(form)) {
                const syncHandler = () => {
                    const formInputs = form.querySelectorAll('[data-flatpickr]');
                    formInputs.forEach((field) => {
                        const picker = field._flatpickr;
                        if (!picker) {
                            return;
                        }
                        if (picker.config.altInput && picker.altInput) {
                            const altValue = picker.altInput.value;
                            if (altValue) {
                                picker.setDate(altValue, false, picker.config.altFormat);
                            } else if (field.value) {
                                picker.setDate(field.value, false, picker.config.dateFormat);
                            }
                        }
                    });
                };
                form.addEventListener('submit', syncHandler);
                formListeners.add(form);
            }
        });
    }

    function initDatePickers({ fieldSelectors, modalSelectors } = {}) {
        const selectors = getSelectors(fieldSelectors);
        initialiseInputs(collectInputs(selectors));

        const modalSelectorList = toArray(modalSelectors).filter(Boolean);
        if (modalSelectorList.length === 0) {
            return;
        }

        modalSelectorList.forEach((modalSelector) => {
            document.querySelectorAll(modalSelector).forEach((modal) => {
                if (!modal || modalListeners.has(modal)) {
                    return;
                }
                modal.addEventListener('shown.bs.modal', () => {
                    initialiseInputs(collectInputs(selectors, modal));
                });
                modalListeners.add(modal);
            });
        });
    }

    global.initDatePickers = initDatePickers;
})(window);
