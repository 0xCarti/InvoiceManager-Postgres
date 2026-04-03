(function (window, document) {
    'use strict';

    var scannerCounter = 0;

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

    function initRemovalHandling(container, buttonSelector, rowSelector) {
        if (!container) {
            return;
        }

        container.addEventListener('click', function (event) {
            var button = event.target.closest(buttonSelector);
            if (!button) {
                return;
            }
            var row = button.closest(rowSelector);
            if (row) {
                row.remove();
            }
        });
    }

    function initRepeater(form, options) {
        var container = form.querySelector(options.containerSelector);
        var templateEl = form.querySelector(options.templateSelector);
        var addButton = form.querySelector(options.addButtonSelector);
        if (!container || !templateEl || !addButton) {
            return null;
        }

        var nextIndexAttr = form.getAttribute(options.nextIndexAttr);
        var nextIndex = parseInt(nextIndexAttr || '', 10);
        if (isNaN(nextIndex)) {
            nextIndex = container.querySelectorAll(options.rowSelector).length;
        }

        addButton.addEventListener('click', function () {
            var newRow = createUnitRow(templateEl, nextIndex);
            if (newRow) {
                container.appendChild(newRow);
                nextIndex += 1;
                form.setAttribute(options.nextIndexAttr, String(nextIndex));
            }
        });

        initRemovalHandling(
            container,
            options.removeButtonSelector,
            options.rowSelector
        );

        return container;
    }

    function createBarcodeScannerController(form) {
        var panel = form.querySelector('[data-barcode-scanner-panel]');
        var readerShell = form.querySelector('[data-barcode-reader-shell]');
        var reader = form.querySelector('[data-barcode-reader]');
        var status = form.querySelector('[data-barcode-status]');
        var targetLabel = form.querySelector('[data-barcode-scan-target]');
        var stopButton = form.querySelector('[data-barcode-stop]');
        if (!panel || !readerShell || !reader || !status || !targetLabel || !stopButton) {
            return null;
        }

        scannerCounter += 1;
        reader.id = reader.id || ('item-barcode-reader-' + String(scannerCounter));

        var state = {
            activeInput: null,
            busy: false,
            handlingScan: false,
            scanner: null,
            scanning: false,
            stopPromise: null
        };

        function isCameraContextAvailable() {
            return window.isSecureContext
                || window.location.hostname === 'localhost'
                || window.location.hostname === '127.0.0.1';
        }

        function setStatus(message, tone) {
            status.className = 'alert py-2 px-3 small mb-3 alert-' + (tone || 'secondary');
            status.textContent = message;
        }

        function describeInput(input) {
            if (!input) {
                return 'Tap Scan beside a barcode field to start the camera.';
            }
            var inputs = Array.prototype.slice.call(
                form.querySelectorAll('.barcode-row input[name^="barcodes-"]')
            );
            var index = inputs.indexOf(input);
            if (index === -1) {
                return 'Scanning into the selected barcode field.';
            }
            return 'Scanning into barcode ' + String(index + 1) + '.';
        }

        function showPanel() {
            panel.classList.remove('d-none');
        }

        function hideReader() {
            readerShell.classList.add('d-none');
            stopButton.classList.add('d-none');
        }

        function showReader() {
            showPanel();
            readerShell.classList.remove('d-none');
            stopButton.classList.remove('d-none');
        }

        async function stopScanner(options) {
            options = options || {};
            var keepPanel = !!options.keepPanel;
            var message = options.message || '';
            var tone = options.tone || 'secondary';

            if (state.stopPromise) {
                return state.stopPromise;
            }

            if (!state.scanner) {
                state.activeInput = null;
                state.handlingScan = false;
                state.scanning = false;
                if (keepPanel) {
                    showPanel();
                    hideReader();
                    targetLabel.textContent = 'Tap Scan beside a barcode field to start the camera.';
                    if (message) {
                        setStatus(message, tone);
                    }
                } else {
                    panel.classList.add('d-none');
                    hideReader();
                    targetLabel.textContent = 'Tap Scan beside a barcode field to start the camera.';
                }
                return Promise.resolve();
            }

            stopButton.disabled = true;
            state.stopPromise = (async function () {
                try {
                    if (state.scanning) {
                        await state.scanner.stop();
                    }
                } catch (error) {
                    // Ignore stop errors; we still want to release the UI state.
                }
                try {
                    await state.scanner.clear();
                } catch (error) {
                    // Ignore clear errors from partially started scanner sessions.
                }

                reader.innerHTML = '';
                state.scanner = null;
                state.scanning = false;
                state.activeInput = null;
                state.handlingScan = false;
                stopButton.disabled = false;
                targetLabel.textContent = 'Tap Scan beside a barcode field to start the camera.';

                if (keepPanel) {
                    showPanel();
                    hideReader();
                    if (message) {
                        setStatus(message, tone);
                    }
                } else {
                    panel.classList.add('d-none');
                    hideReader();
                }

                state.stopPromise = null;
            }());

            return state.stopPromise;
        }

        async function startScannerForInput(input) {
            if (!input || state.busy) {
                return;
            }

            if (!window.Html5Qrcode) {
                showPanel();
                hideReader();
                setStatus(
                    'The camera scanner could not be loaded. You can still type the barcode or use a handheld scanner.',
                    'warning'
                );
                return;
            }

            if (!isCameraContextAvailable() || !navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                showPanel();
                hideReader();
                setStatus(
                    'Camera scanning requires HTTPS or localhost in a browser that allows camera access.',
                    'warning'
                );
                return;
            }

            state.busy = true;

            if (state.scanner || state.scanning) {
                await stopScanner({ keepPanel: false });
            }

            state.activeInput = input;
            targetLabel.textContent = describeInput(input);
            setStatus('Opening the camera. Point it at the barcode.', 'info');
            showReader();

            var scanner = new window.Html5Qrcode(reader.id);
            state.scanner = scanner;

            var onScanSuccess = function (decodedText) {
                var value;
                if (state.handlingScan) {
                    return;
                }

                value = (decodedText || '').trim();
                if (!value) {
                    return;
                }

                state.handlingScan = true;
                input.value = value;
                input.dispatchEvent(new window.Event('input', { bubbles: true }));
                input.dispatchEvent(new window.Event('change', { bubbles: true }));
                input.focus();

                stopScanner({
                    keepPanel: true,
                    message: 'Captured barcode ' + value + '.',
                    tone: 'success'
                }).catch(function () {
                    setStatus('Captured barcode ' + value + '.', 'success');
                });
            };

            try {
                await scanner.start(
                    { facingMode: { exact: 'environment' } },
                    { fps: 10 },
                    onScanSuccess
                );
            } catch (primaryError) {
                try {
                    await scanner.start(
                        { facingMode: 'environment' },
                        { fps: 10 },
                        onScanSuccess
                    );
                } catch (fallbackError) {
                    await stopScanner({
                        keepPanel: true,
                        message: 'Unable to open the camera. Check permission settings and try again.',
                        tone: 'warning'
                    });
                    state.busy = false;
                    return;
                }
            }

            state.scanning = true;
            state.busy = false;
            setStatus('Camera is live. Center the barcode in the frame.', 'info');
        }

        stopButton.addEventListener('click', function () {
            stopScanner({ keepPanel: false });
        });

        form.addEventListener('click', function (event) {
            var removeButton = event.target.closest('.remove-barcode');
            var button = event.target.closest('.scan-barcode');
            var row;
            var input;

            if (removeButton) {
                row = removeButton.closest('.barcode-row');
                if (row && state.activeInput && row.contains(state.activeInput)) {
                    stopScanner({ keepPanel: false });
                }
                return;
            }

            if (!button) {
                return;
            }

            row = button.closest('.barcode-row');
            input = row ? row.querySelector('input[name^="barcodes-"]') : null;
            startScannerForInput(input);
        });

        form.addEventListener('submit', function () {
            stopScanner({ keepPanel: false });
        });

        return {
            stop: function () {
                return stopScanner({ keepPanel: false });
            }
        };
    }

    function initItemForm(form) {
        if (!form || form.dataset.itemFormInitialized === 'true') {
            return;
        }

        var unitsContainer = initRepeater(form, {
            containerSelector: '#units-container',
            templateSelector: '#unit-row-template',
            addButtonSelector: '#add-unit',
            rowSelector: '.unit-row',
            removeButtonSelector: '.remove-unit',
            nextIndexAttr: 'data-next-index'
        });
        if (unitsContainer) {
            initDefaultsHandling(unitsContainer);
        }

        initRepeater(form, {
            containerSelector: '#barcodes-container',
            templateSelector: '#barcode-row-template',
            addButtonSelector: '#add-barcode',
            rowSelector: '.barcode-row',
            removeButtonSelector: '.remove-barcode',
            nextIndexAttr: 'data-barcode-next-index'
        });

        form._barcodeScannerController = createBarcodeScannerController(form);

        if (form.closest('.modal')) {
            var modal = form.closest('.modal');
            if (modal && modal.dataset.itemBarcodeScannerBound !== 'true') {
                modal.addEventListener('hidden.bs.modal', function () {
                    var activeForm = modal.querySelector('form[data-item-form]');
                    if (activeForm && activeForm._barcodeScannerController) {
                        activeForm._barcodeScannerController.stop();
                    }
                });
                modal.dataset.itemBarcodeScannerBound = 'true';
            }
        }

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
