(function (window, document) {
    'use strict';

    function getCellValue(row, index) {
        const cell = row.cells[index];
        if (!cell) {
            return '';
        }
        const dataValue = cell.getAttribute('data-sort-value');
        return (dataValue !== null ? dataValue : cell.textContent).trim();
    }

    function parseNumber(value) {
        if (typeof value === 'number') {
            return value;
        }
        if (typeof value !== 'string') {
            return NaN;
        }
        const normalized = value.replace(/,/g, '').trim();
        if (!normalized) {
            return NaN;
        }
        const parsed = parseFloat(normalized);
        return Number.isNaN(parsed) ? NaN : parsed;
    }

    function updateIndicators(headers, activeHeader, direction) {
        headers.forEach((header) => {
            if (header === activeHeader) {
                header.setAttribute('aria-sort', direction === 'asc' ? 'ascending' : 'descending');
                let indicator = header.querySelector('.sort-indicator');
                if (!indicator) {
                    indicator = document.createElement('span');
                    indicator.className = 'sort-indicator';
                    indicator.style.marginLeft = '0.25rem';
                    header.appendChild(indicator);
                }
                indicator.textContent = direction === 'asc' ? '▲' : '▼';
            } else {
                header.removeAttribute('aria-sort');
                delete header.dataset.sortDirection;
                const indicator = header.querySelector('.sort-indicator');
                if (indicator) {
                    indicator.remove();
                }
            }
        });
    }

    function bindSortableTable(table) {
        if (!table || table.dataset.sortableInitialized === 'true') {
            return;
        }

        const head = table.tHead;
        const body = table.tBodies[0];
        if (!head || !body) {
            return;
        }

        const headers = Array.from(head.querySelectorAll('th'));
        headers.forEach((th, index) => {
            if (th.dataset.sortable === 'false') {
                return;
            }
            th.style.cursor = 'pointer';
            th.addEventListener('click', (event) => {
                if (event.target.closest('button, a, input, select, textarea, label')) {
                    return;
                }

                const currentDirection = th.dataset.sortDirection === 'asc' ? 'desc' : 'asc';
                const type = th.dataset.type === 'number' ? 'number' : 'text';
                const rows = Array.from(body.rows);

                rows.sort((rowA, rowB) => {
                    const valueA = getCellValue(rowA, index);
                    const valueB = getCellValue(rowB, index);

                    if (type === 'number') {
                        const numA = parseNumber(valueA);
                        const numB = parseNumber(valueB);
                        const safeA = Number.isNaN(numA) ? Number.NEGATIVE_INFINITY : numA;
                        const safeB = Number.isNaN(numB) ? Number.NEGATIVE_INFINITY : numB;

                        if (safeA === safeB) {
                            return 0;
                        }
                        if (currentDirection === 'asc') {
                            return safeA < safeB ? -1 : 1;
                        }
                        return safeA > safeB ? -1 : 1;
                    }

                    const textA = valueA.toLowerCase();
                    const textB = valueB.toLowerCase();

                    if (textA === textB) {
                        return 0;
                    }
                    if (currentDirection === 'asc') {
                        return textA.localeCompare(textB);
                    }
                    return textB.localeCompare(textA);
                });

                rows.forEach((row) => body.appendChild(row));

                th.dataset.sortDirection = currentDirection;
                updateIndicators(headers, th, currentDirection);
            });
        });

        table.dataset.sortableInitialized = 'true';
    }

    function initAllSortableTables(root) {
        const context = root || document;
        const tables = context.querySelectorAll('table.sortable');
        tables.forEach((table) => bindSortableTable(table));
    }

    window.TableSort = window.TableSort || {};
    window.TableSort.bind = bindSortableTable;
    window.TableSort.initAll = initAllSortableTables;

    document.addEventListener('DOMContentLoaded', () => {
        initAllSortableTables(document);
    });
}(window, document));
