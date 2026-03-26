function initVendorAliasResolution(config) {
    const container = config && config.container ? config.container : null;
    if (!container) {
        return;
    }

    const unitsMap = (config && config.unitsMap) || {};
    const lineDetails = (config && Array.isArray(config.lineDetails)
        ? config.lineDetails
        : []);
    const rows = container.querySelectorAll('[data-role="alias-row"]');
    const csrfTokenInput = document.querySelector('input[name="csrf_token"]');
    const quickAddButtons = container.querySelectorAll('[data-role="quick-add-item"]');
    const newItemModalEl = document.getElementById('newItemModal');
    const saveNewItemButton = document.getElementById('save-new-item');
    const addNewItemUnitButton = document.getElementById('add-new-item-unit');
    const newItemUnitsContainer = document.getElementById('new-item-units');
    const baseUnitSelect = document.getElementById('new-item-base-unit');
    const newItemNameInput = document.getElementById('new-item-name');
    let validationMessageEl = null;
    let activeRow = null;
    let newItemUnitIndex = 0;
    let newItemModal = null;

    if (newItemModalEl && typeof bootstrap !== 'undefined') {
        newItemModal =
            bootstrap.Modal.getInstance(newItemModalEl) ||
            new bootstrap.Modal(newItemModalEl);
    }

    function selectEnhancerAvailable() {
        return typeof $ !== 'undefined' && $.fn && $.fn.select2;
    }

    function enhanceItemSelect(select) {
        if (!select || !selectEnhancerAvailable()) {
            return;
        }
        const $select = $(select);
        if (!$select.data('select2')) {
            $select.select2({ width: '100%' });
        }
    }

    function setItemSelectValue(select, value) {
        if (!select) {
            return;
        }
        const stringValue = value !== undefined && value !== null ? String(value) : '';
        select.value = stringValue;
        if (selectEnhancerAvailable()) {
            const $select = $(select);
            if ($select.data('select2')) {
                $select.val(stringValue).trigger('change');
                return;
            }
        }
        const changeEvent = new Event('change', { bubbles: true });
        select.dispatchEvent(changeEvent);
    }

    function getValidationMessageElement() {
        if (validationMessageEl) {
            return validationMessageEl;
        }
        if (!newItemModalEl) {
            return null;
        }
        const modalBody = newItemModalEl.querySelector('.modal-body');
        if (!modalBody) {
            return null;
        }
        const alert = document.createElement('div');
        alert.classList.add('alert', 'alert-danger', 'mt-2');
        alert.setAttribute('role', 'alert');
        alert.classList.add('d-none');
        modalBody.appendChild(alert);
        validationMessageEl = alert;
        return validationMessageEl;
    }

    function showValidationMessage(messages) {
        const target = getValidationMessageElement();
        if (!target) {
            window.alert(Array.isArray(messages) ? messages.join(' ') : messages);
            return;
        }
        const messageList = Array.isArray(messages) ? messages : [messages];
        target.innerHTML = '';
        messageList.forEach((message) => {
            const line = document.createElement('div');
            line.textContent = message;
            target.appendChild(line);
        });
        target.classList.remove('d-none');
    }

    function clearValidationMessage() {
        const target = getValidationMessageElement();
        if (!target) {
            return;
        }
        target.classList.add('d-none');
        target.innerHTML = '';
    }

    function syncBaseUnitRow(force = false) {
        if (!newItemUnitsContainer) {
            return;
        }
        const baseRow = newItemUnitsContainer.querySelector(
            '.new-item-unit-row[data-base="true"]'
        );
        if (!baseRow) {
            return;
        }
        const nameInput = baseRow.querySelector('.new-item-unit-name');
        if (nameInput && baseUnitSelect && (force || !nameInput.value)) {
            nameInput.value = baseUnitSelect.value || '';
        }
    }

    function parsePackSizeText(packSizeText) {
        if (!packSizeText || typeof packSizeText !== 'string') {
            return { packCount: null, sizeText: '', baseUnit: null, baseQuantity: null };
        }
        const normalized = packSizeText.trim();
        if (!normalized) {
            return { packCount: null, sizeText: '', baseUnit: null, baseQuantity: null };
        }

        let packCount = null;
        let sizeText = normalized;

        const splitMatch = normalized.match(/^(\d+)\s*[\/x]\s*(.+)$/i);
        if (splitMatch) {
            packCount = parseInt(splitMatch[1], 10) || null;
            sizeText = splitMatch[2].trim();
        }

        if (packCount === null) {
            const leadingNumber = normalized.match(/^(\d+)\s+(.+)$/);
            if (leadingNumber) {
                packCount = parseInt(leadingNumber[1], 10) || null;
                sizeText = leadingNumber[2].trim();
            }
        }

        const unitDefinitions = [
            { baseUnit: 'gram', aliases: ['g', 'gr', 'gram', 'grams'], multiplier: 1 },
            {
                baseUnit: 'gram',
                aliases: ['kg', 'kilo', 'kilos', 'kilogram', 'kilograms'],
                multiplier: 1000,
            },
            { baseUnit: 'ounce', aliases: ['oz', 'ounce', 'ounces'], multiplier: 1 },
            {
                baseUnit: 'millilitre',
                aliases: ['ml', 'millilitre', 'milliliter', 'millilitres', 'milliliters'],
                multiplier: 1,
            },
            {
                baseUnit: 'millilitre',
                aliases: ['l', 'lt', 'liter', 'litre', 'liters', 'litres'],
                multiplier: 1000,
            },
            { baseUnit: 'each', aliases: ['ea', 'each'], multiplier: 1 },
        ];

        const unitMatch = sizeText.match(/^(\d+(?:\.\d+)?)\s*([a-zA-Z]+)$/i);
        if (!unitMatch) {
            return { packCount, sizeText, baseUnit: null, baseQuantity: null };
        }

        const quantity = parseFloat(unitMatch[1]);
        const rawUnit = unitMatch[2].replace(/\./g, '').toLowerCase();
        const matchedUnit = unitDefinitions.find((unit) =>
            unit.aliases.some((alias) => rawUnit === alias.toLowerCase())
        );

        if (!matchedUnit || !Number.isFinite(quantity)) {
            return { packCount, sizeText, baseUnit: null, baseQuantity: null };
        }

        return {
            packCount,
            sizeText,
            baseUnit: matchedUnit.baseUnit,
            baseQuantity: quantity * matchedUnit.multiplier,
        };
    }

    function ensureUnitDefaults() {
        if (!newItemUnitsContainer) {
            return;
        }
        const receivingChecked = newItemUnitsContainer.querySelector(
            '.new-item-unit-receiving:checked'
        );
        if (!receivingChecked) {
            const fallback = newItemUnitsContainer.querySelector(
                '.new-item-unit-row[data-base="true"] .new-item-unit-receiving'
            );
            if (fallback) {
                fallback.checked = true;
            }
        }

        const transferChecked = newItemUnitsContainer.querySelector(
            '.new-item-unit-transfer:checked'
        );
        if (!transferChecked) {
            const fallback = newItemUnitsContainer.querySelector(
                '.new-item-unit-row[data-base="true"] .new-item-unit-transfer'
            );
            if (fallback) {
                fallback.checked = true;
            }
        }
    }

    function createNewItemUnitRow(options = {}) {
        const index = newItemUnitIndex++;
        const isBase = Boolean(options.isBase);
        const row = document.createElement('div');
        row.classList.add('row', 'g-2', 'align-items-end', 'new-item-unit-row');
        row.dataset.base = isBase ? 'true' : 'false';

        const nameCol = document.createElement('div');
        nameCol.classList.add('col-12', 'col-md-4');
        const nameLabel = document.createElement('label');
        nameLabel.classList.add('form-label');
        const nameId = `new-item-unit-${index}-name`;
        nameLabel.setAttribute('for', nameId);
        nameLabel.textContent = isBase ? 'Base Unit' : 'Unit Name';
        const nameInput = document.createElement('input');
        nameInput.type = 'text';
        nameInput.classList.add('form-control', 'new-item-unit-name');
        nameInput.id = nameId;
        nameInput.value = options.name || '';
        if (isBase) {
            nameInput.readOnly = true;
        } else {
            nameInput.placeholder = 'e.g. Case';
        }
        nameCol.append(nameLabel, nameInput);
        row.appendChild(nameCol);

        const factorCol = document.createElement('div');
        factorCol.classList.add('col-12', 'col-md-3');
        const factorLabel = document.createElement('label');
        factorLabel.classList.add('form-label');
        const factorId = `new-item-unit-${index}-factor`;
        factorLabel.setAttribute('for', factorId);
        factorLabel.textContent = 'Ratio to Base Unit';
        const factorInput = document.createElement('input');
        factorInput.type = 'number';
        factorInput.classList.add('form-control', 'new-item-unit-factor');
        factorInput.id = factorId;
        factorInput.step = 'any';
        factorInput.min = '0';
        factorInput.value =
            options.factor !== undefined && options.factor !== null
                ? options.factor
                : 1;
        if (isBase) {
            factorInput.readOnly = true;
        }
        factorCol.append(factorLabel, factorInput);
        row.appendChild(factorCol);

        const receivingCol = document.createElement('div');
        receivingCol.classList.add('col-6', 'col-md-2', 'd-flex', 'align-items-center');
        const receivingWrapper = document.createElement('div');
        receivingWrapper.classList.add('form-check');
        const receivingId = `new-item-unit-${index}-receiving`;
        const receivingInput = document.createElement('input');
        receivingInput.type = 'radio';
        receivingInput.classList.add('form-check-input', 'new-item-unit-receiving');
        receivingInput.name = 'new-item-receiving-default';
        receivingInput.id = receivingId;
        if (options.receivingDefault) {
            receivingInput.checked = true;
        }
        const receivingLabel = document.createElement('label');
        receivingLabel.classList.add('form-check-label');
        receivingLabel.setAttribute('for', receivingId);
        receivingLabel.textContent = 'Receiving Default';
        receivingWrapper.append(receivingInput, receivingLabel);
        receivingCol.appendChild(receivingWrapper);
        row.appendChild(receivingCol);

        const transferCol = document.createElement('div');
        transferCol.classList.add('col-6', 'col-md-2', 'd-flex', 'align-items-center');
        const transferWrapper = document.createElement('div');
        transferWrapper.classList.add('form-check');
        const transferId = `new-item-unit-${index}-transfer`;
        const transferInput = document.createElement('input');
        transferInput.type = 'radio';
        transferInput.classList.add('form-check-input', 'new-item-unit-transfer');
        transferInput.name = 'new-item-transfer-default';
        transferInput.id = transferId;
        if (options.transferDefault) {
            transferInput.checked = true;
        }
        const transferLabel = document.createElement('label');
        transferLabel.classList.add('form-check-label');
        transferLabel.setAttribute('for', transferId);
        transferLabel.textContent = 'Transfer Default';
        transferWrapper.append(transferInput, transferLabel);
        transferCol.appendChild(transferWrapper);
        row.appendChild(transferCol);

        if (!isBase) {
            const removeCol = document.createElement('div');
            removeCol.classList.add('col-12', 'col-md-1', 'd-flex', 'align-items-center');
            const removeButton = document.createElement('button');
            removeButton.type = 'button';
            removeButton.classList.add(
                'btn',
                'btn-outline-danger',
                'btn-sm',
                'w-100',
                'new-item-unit-remove'
            );
            removeButton.textContent = 'Remove';
            removeButton.setAttribute('aria-label', 'Remove unit');
            removeCol.appendChild(removeButton);
            row.appendChild(removeCol);
        }

        return row;
    }

    function appendNewItemUnitRow(options = {}) {
        if (!newItemUnitsContainer) {
            return null;
        }
        const row = createNewItemUnitRow(options);
        newItemUnitsContainer.appendChild(row);
        ensureUnitDefaults();
        return row;
    }

    function resetNewItemUnitRows(options = {}) {
        if (!newItemUnitsContainer) {
            return;
        }
        newItemUnitsContainer.innerHTML = '';
        newItemUnitIndex = 0;
        const baseName =
            options.baseName || (baseUnitSelect ? baseUnitSelect.value || '' : 'Each');
        const baseFactor =
            options.baseFactor !== undefined && options.baseFactor !== null
                ? options.baseFactor
                : 1;
        appendNewItemUnitRow({
            name: baseName,
            factor: baseFactor,
            receivingDefault: true,
            transferDefault: true,
            isBase: true,
        });

        if (options.caseFactor && Number(options.caseFactor) > 1) {
            appendNewItemUnitRow({
                name: options.caseName || 'Case',
                factor: Number(options.caseFactor),
                receivingDefault: false,
                transferDefault: false,
            });
        }

        syncBaseUnitRow();
        ensureUnitDefaults();
    }

    resetNewItemUnitRows();

    function getRowPackSize(row) {
        if (!row) {
            return '';
        }
        const dataValue = row.getAttribute('data-pack-size');
        if (dataValue) {
            return dataValue;
        }
        const lineIndex = row.getAttribute('data-line-index');
        if (
            lineIndex !== null &&
            lineIndex !== undefined &&
            lineDetails[lineIndex] &&
            lineDetails[lineIndex].pack_size
        ) {
            return lineDetails[lineIndex].pack_size;
        }
        return '';
    }

    function seedUnitsFromPackSize(row) {
        const packSizeText = getRowPackSize(row);
        const { packCount, sizeText, baseUnit, baseQuantity } = parsePackSizeText(
            packSizeText
        );
        const resolvedBaseUnit = baseUnit || 'each';
        const numericBaseQuantity =
            baseUnit && Number.isFinite(Number(baseQuantity)) && Number(baseQuantity) > 0
                ? Number(baseQuantity)
                : null;
        const normalizedBaseFactor = numericBaseQuantity || 1;

        if (baseUnitSelect) {
            const currentValue = baseUnitSelect.value;
            if (currentValue !== resolvedBaseUnit) {
                baseUnitSelect.value = resolvedBaseUnit;
                const changeEvent = new Event('change', { bubbles: true });
                baseUnitSelect.dispatchEvent(changeEvent);
            }
        }

        const baseName = baseUnit ? resolvedBaseUnit : 'Each';
        const caseFactor =
            packCount && packCount > 1
                ? numericBaseQuantity
                    ? packCount * numericBaseQuantity
                    : packCount
                : null;
        resetNewItemUnitRows({
            baseName,
            baseFactor: normalizedBaseFactor,
            caseFactor,
        });
    }

    function populateUnits(select, itemId, preferredUnitId) {
        if (!select) {
            return;
        }
        const units = unitsMap[itemId] || [];
        const selected = preferredUnitId || select.getAttribute('data-selected');
        const currentValue = select.value;
        select.innerHTML = '';
        const placeholder = document.createElement('option');
        placeholder.value = '';
        placeholder.textContent = 'Select unit';
        select.appendChild(placeholder);
        units.forEach((unit) => {
            const opt = document.createElement('option');
            opt.value = unit.id;
            opt.textContent = unit.name;
            if (
                String(unit.id) === String(selected) ||
                String(unit.id) === String(currentValue) ||
                (!selected && unit.receiving_default)
            ) {
                opt.selected = true;
            }
            select.appendChild(opt);
        });
    }

    function buildUnitsPayload(baseUnit) {
        if (!newItemUnitsContainer) {
            return {
                unitsPayload: [],
                hasInvalidUnits: true,
                hasReceivingDefault: false,
                hasTransferDefault: false,
            };
        }
        const unitRows = Array.from(
            newItemUnitsContainer.querySelectorAll('.new-item-unit-row')
        );
        const unitsPayload = [];
        let hasInvalidUnits = false;
        let hasReceivingDefault = false;
        let hasTransferDefault = false;

        unitRows.forEach((row) => {
            const nameField = row.querySelector('.new-item-unit-name');
            const factorField = row.querySelector('.new-item-unit-factor');
            const receivingField = row.querySelector('.new-item-unit-receiving');
            const transferField = row.querySelector('.new-item-unit-transfer');

            let unitName = nameField ? nameField.value.trim() : '';
            const factorValue = factorField ? parseFloat(factorField.value) : NaN;

            if (row.dataset.base === 'true') {
                if (baseUnit) {
                    unitName = baseUnit;
                }
            }

            const receivingDefault = receivingField ? receivingField.checked : false;
            const transferDefault = transferField ? transferField.checked : false;

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
                factor: row.dataset.base === 'true' ? 1 : factorValue,
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

    function upsertItemOption(select, itemId, itemName) {
        if (!select) {
            return;
        }
        const optionValue = String(itemId);
        const existingOption = Array.from(select.options).find(
            (opt) => opt.value === optionValue
        );
        if (existingOption) {
            existingOption.textContent = itemName;
            return;
        }
        const option = document.createElement('option');
        option.value = optionValue;
        option.textContent = itemName;
        select.appendChild(option);
    }

    function updateUnitsMap(itemId, unitsPayload) {
        unitsMap[itemId] = Array.isArray(unitsPayload)
            ? unitsPayload.map((unit) => ({
                  id: unit.id,
                  name: unit.name,
                  receiving_default: unit.receiving_default,
              }))
            : [];
    }

    function handleNewItemSuccess(itemData, unitsData) {
        if (!itemData || !itemData.id) {
            return;
        }

        const unitsList = unitsData && Array.isArray(unitsData.units)
            ? unitsData.units
            : [];
        const preferredUnit = unitsList.find((unit) => unit.receiving_default) || unitsList[0] || null;

        updateUnitsMap(itemData.id, unitsList);

        rows.forEach((row) => {
            const itemSelect = row.querySelector('[data-role="alias-item-select"]');
            upsertItemOption(itemSelect, itemData.id, itemData.name);
        });

        if (activeRow) {
            const itemSelect = activeRow.querySelector('[data-role="alias-item-select"]');
            const unitSelect = activeRow.querySelector('[data-role="alias-unit-select"]');
            if (itemSelect) {
                setItemSelectValue(itemSelect, itemData.id);
            }
            if (unitSelect) {
                const preferredId = preferredUnit ? preferredUnit.id : null;
                if (preferredId) {
                    unitSelect.setAttribute('data-selected', preferredId);
                }
                populateUnits(unitSelect, itemData.id, preferredUnit ? preferredUnit.id : null);
            }
        }

        if (newItemNameInput) {
            newItemNameInput.value = '';
        }
        resetNewItemUnitRows();
        clearValidationMessage();
        if (newItemModal) {
            newItemModal.hide();
        }
        activeRow = null;
    }

    rows.forEach((row) => {
        const itemSelect = row.querySelector('[data-role="alias-item-select"]');
        const unitSelect = row.querySelector('[data-role="alias-unit-select"]');
        if (!itemSelect || !unitSelect) {
            return;
        }
        itemSelect.addEventListener('change', (event) => {
            populateUnits(unitSelect, event.target.value);
        });
        populateUnits(unitSelect, itemSelect.value);
        enhanceItemSelect(itemSelect);
    });

    quickAddButtons.forEach((button) => {
        button.addEventListener('click', (event) => {
            event.preventDefault();
            activeRow = button.closest('[data-role="alias-row"]');
            seedUnitsFromPackSize(activeRow);
            clearValidationMessage();
            if (newItemNameInput) {
                newItemNameInput.focus();
                newItemNameInput.value = '';
            }
            if (!newItemModal) {
                window.alert(
                    'Unable to open the new item modal. Please try reloading the page.'
                );
                return;
            }
            newItemModal.show();
        });
    });

    if (baseUnitSelect) {
        baseUnitSelect.addEventListener('change', () => {
            syncBaseUnitRow(true);
            ensureUnitDefaults();
        });
    }

    if (addNewItemUnitButton) {
        addNewItemUnitButton.addEventListener('click', () => {
            const row = appendNewItemUnitRow({
                name: '',
                factor: 1,
                receivingDefault: false,
                transferDefault: false,
            });
            if (row) {
                const nameInput = row.querySelector('.new-item-unit-name');
                if (nameInput && !nameInput.readOnly) {
                    nameInput.focus();
                }
            }
        });
    }

    if (newItemUnitsContainer) {
        newItemUnitsContainer.addEventListener('click', (event) => {
            const target = event.target;
            if (!(target instanceof Element)) {
                return;
            }
            const removeButton = target.closest('.new-item-unit-remove');
            if (!removeButton) {
                return;
            }
            const row = removeButton.closest('.new-item-unit-row');
            if (row && row.dataset.base !== 'true') {
                row.remove();
                ensureUnitDefaults();
            }
        });
    }

    if (saveNewItemButton) {
        saveNewItemButton.addEventListener('click', () => {
            const glCodeSelect = document.getElementById('new-item-gl-code');
            const name = newItemNameInput ? newItemNameInput.value.trim() : '';
            const glCode = glCodeSelect ? glCodeSelect.value : null;
            const baseUnit = baseUnitSelect ? baseUnitSelect.value : '';
            const csrfToken = csrfTokenInput ? csrfTokenInput.value : null;

            const {
                unitsPayload,
                hasInvalidUnits,
                hasReceivingDefault,
                hasTransferDefault,
            } = buildUnitsPayload(baseUnit);

            const validationErrors = [];
            if (!name) {
                validationErrors.push('Item name is required.');
            }
            if (!glCode) {
                validationErrors.push('Purchase GL Code is required.');
            }
            if (!baseUnit) {
                validationErrors.push('Base unit selection is required.');
            }
            if (!unitsPayload.length || hasInvalidUnits) {
                validationErrors.push(
                    'Add at least one valid unit with a ratio greater than zero.'
                );
            }
            if (!hasReceivingDefault || !hasTransferDefault) {
                validationErrors.push(
                    'Please select both a receiving default and a transfer default.'
                );
            }

            if (validationErrors.length) {
                showValidationMessage(validationErrors);
                return;
            }

            clearValidationMessage();

            fetch('/items/quick_add', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken || '',
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
                        throw new Error('Unable to create item');
                    }
                    return response.json();
                })
                .then((data) => {
                    if (!data || !data.id) {
                        throw new Error('Invalid response while creating item');
                    }
                    return fetch(`/items/${data.id}/units`)
                        .then((response) => {
                            if (!response.ok) {
                                throw new Error('Failed to load units');
                            }
                            return response.json();
                        })
                        .then((unitsData) => {
                            handleNewItemSuccess(data, unitsData);
                        })
                        .catch((error) => {
                            console.error('Error fetching units for new item', error);
                            handleNewItemSuccess(data, { units: [] });
                        });
                })
                .catch((error) => {
                    console.error('Error creating new item from alias mapping', error);
                    showValidationMessage(
                        'Unable to create item right now. Please check the form and try again.'
                    );
                    window.alert('Unable to create item. Please try again.');
                });
        });
    }
}
