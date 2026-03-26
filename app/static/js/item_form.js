(function (window, document) {
    'use strict';

    function replacePlaceholderValues(node, placeholder, replacement) {
        if (!node) {
            return;
        }

        var ELEMENT_NODE = (window.Node && window.Node.ELEMENT_NODE) || 1;
        var TEXT_NODE = (window.Node && window.Node.TEXT_NODE) || 3;

        if (node.nodeType === ELEMENT_NODE) {
            Array.prototype.forEach.call(node.attributes || [], function (attr) {
                if (attr.value && attr.value.indexOf(placeholder) !== -1) {
                    node.setAttribute(attr.name, attr.value.split(placeholder).join(replacement));
                }
            });
        } else if (node.nodeType === TEXT_NODE && node.nodeValue.indexOf(placeholder) !== -1) {
            node.nodeValue = node.nodeValue.split(placeholder).join(replacement);
        }

        Array.prototype.forEach.call(node.childNodes || [], function (child) {
            replacePlaceholderValues(child, placeholder, replacement);
        });
    }

    function createUnitRow(templateEl, index) {
        if (!templateEl) {
            return null;
        }

        var newRow = null;

        if (templateEl.tagName === 'TEMPLATE' && 'content' in templateEl) {
            var fragment = templateEl.content.cloneNode(true);
            newRow = fragment.firstElementChild;
        } else {
            var wrapper = document.createElement('div');
            wrapper.appendChild(templateEl.cloneNode(true));
            newRow = wrapper.firstElementChild;
        }

        if (!newRow) {
            return null;
        }

        replacePlaceholderValues(newRow, '__index__', String(index));
        return newRow;
    }

    function initDefaultsHandling(unitsContainer) {
        unitsContainer.addEventListener('change', function (event) {
            var target = event.target;
            if (target.classList.contains('default-receiving') && target.checked) {
                unitsContainer.querySelectorAll('.default-receiving').forEach(function (checkbox) {
                    if (checkbox !== target) {
                        checkbox.checked = false;
                    }
                });
            } else if (target.classList.contains('default-transfer') && target.checked) {
                unitsContainer.querySelectorAll('.default-transfer').forEach(function (checkbox) {
                    if (checkbox !== target) {
                        checkbox.checked = false;
                    }
                });
            }
        });
    }

    function initRemovalHandling(unitsContainer) {
        unitsContainer.addEventListener('click', function (event) {
            var button = event.target.closest('.remove-unit');
            if (!button) {
                return;
            }
            var row = button.closest('.unit-row');
            if (row) {
                row.remove();
            }
        });
    }

    function initItemForm(form) {
        if (!form || form.dataset.itemFormInitialized === 'true') {
            return;
        }

        var unitsContainer = form.querySelector('#units-container');
        var templateEl = form.querySelector('#unit-row-template');
        if (!unitsContainer || !templateEl) {
            form.dataset.itemFormInitialized = 'true';
            return;
        }

        var addButton = form.querySelector('#add-unit');
        if (!addButton) {
            form.dataset.itemFormInitialized = 'true';
            return;
        }

        var nextIndexAttr = form.getAttribute('data-next-index');
        var nextIndex = parseInt(nextIndexAttr || '', 10);
        if (isNaN(nextIndex)) {
            nextIndex = unitsContainer.querySelectorAll('.unit-row').length;
        }

        addButton.addEventListener('click', function () {
            var newRow = createUnitRow(templateEl, nextIndex);
            if (newRow) {
                unitsContainer.appendChild(newRow);
                nextIndex += 1;
                form.setAttribute('data-next-index', String(nextIndex));
            }
        });

        initRemovalHandling(unitsContainer);
        initDefaultsHandling(unitsContainer);

        form.dataset.itemFormInitialized = 'true';
    }

    function init(container) {
        if (!container) {
            return;
        }

        if (container.matches && container.matches('form[data-item-form]')) {
            initItemForm(container);
            return;
        }

        var form = container.querySelector && container.querySelector('form[data-item-form]');
        if (form) {
            initItemForm(form);
        }
    }

    window.ItemForm = window.ItemForm || {};
    window.ItemForm.init = function (container) {
        if (!container) {
            return;
        }
        if (container instanceof window.Element || container === window.document) {
            init(container);
        } else if (typeof container.length === 'number') {
            Array.prototype.forEach.call(container, init);
        }
    };

    document.addEventListener('DOMContentLoaded', function () {
        var forms = document.querySelectorAll('form[data-item-form]');
        if (forms.length) {
            window.ItemForm.init(forms);
        }
    });
}(window, document));
