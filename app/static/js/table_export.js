(function (window, document) {
    "use strict";

    var FORMULA_PREFIX_PATTERN = /^[=+\-@\t\r]/;
    var NON_DATA_HEADER_PATTERN = /^(actions?|select)$/i;

    function normalizeText(value) {
        return (value || "").replace(/\s+/g, " ").trim();
    }

    function isElementVisible(element) {
        if (!element || element.hidden) {
            return false;
        }
        var style = window.getComputedStyle(element);
        if (style.display === "none" || style.visibility === "hidden") {
            return false;
        }
        return element.getClientRects().length > 0;
    }

    function isExportableTable(table) {
        if (!table || table.dataset.exportDisabled === "true") {
            return false;
        }
        if (table.closest(".modal")) {
            return false;
        }
        if (!table.tHead || !table.tBodies.length) {
            return false;
        }
        if (!table.querySelector("tbody tr")) {
            return false;
        }
        if (document.body.classList.contains("report-page") && table.closest(".container")) {
            return true;
        }
        return Boolean(
            table.id ||
            table.classList.contains("sortable") ||
            table.classList.contains("table-mobile-card")
        );
    }

    function isNonDataHeader(cell) {
        if (!cell) {
            return true;
        }
        if (cell.dataset.export === "false") {
            return true;
        }
        var label = normalizeText(cell.textContent);
        if (!label && cell.querySelector("input[type='checkbox']")) {
            return true;
        }
        if (NON_DATA_HEADER_PATTERN.test(label)) {
            return true;
        }
        return /\bactions?\b/i.test(cell.className || "");
    }

    function getHeaderRow(table) {
        var rows = Array.prototype.slice.call(table.tHead.rows || []);
        if (!rows.length) {
            return null;
        }
        return rows[rows.length - 1];
    }

    function getExportColumns(table) {
        var headerRow = getHeaderRow(table);
        if (!headerRow) {
            return [];
        }

        return Array.prototype.slice.call(headerRow.cells).reduce(function (columns, cell, index) {
            if (!isElementVisible(cell) || isNonDataHeader(cell)) {
                return columns;
            }
            columns.push({
                index: index,
                label: normalizeText(cell.dataset.exportLabel || cell.textContent)
            });
            return columns;
        }, []);
    }

    function getControlValue(control) {
        if (!control) {
            return "";
        }
        if (control.matches("select")) {
            return normalizeText(control.options[control.selectedIndex]?.text || control.value);
        }
        if (control.matches("input[type='checkbox'], input[type='radio']")) {
            return control.checked ? "Yes" : "No";
        }
        return control.value || "";
    }

    function getCellText(cell) {
        if (!cell) {
            return "";
        }
        if (cell.dataset.exportValue !== undefined) {
            return normalizeText(cell.dataset.exportValue);
        }

        var clone = cell.cloneNode(true);
        clone.querySelectorAll(".sort-indicator, script, style").forEach(function (node) {
            node.remove();
        });
        clone.querySelectorAll("button, .btn, .dropdown-menu").forEach(function (node) {
            node.remove();
        });

        var text = normalizeText(clone.textContent);
        if (text) {
            return text;
        }

        var control = cell.querySelector("input, select, textarea");
        return normalizeText(getControlValue(control));
    }

    function expandRowCells(row) {
        var expanded = [];
        Array.prototype.slice.call(row.cells || []).forEach(function (cell) {
            var colspan = parseInt(cell.getAttribute("colspan") || "1", 10);
            var value = getCellText(cell);
            expanded.push(value);
            for (var i = 1; i < colspan; i += 1) {
                expanded.push("");
            }
        });
        return expanded;
    }

    function protectSpreadsheetValue(value) {
        var text = value == null ? "" : String(value);
        if (FORMULA_PREFIX_PATTERN.test(text)) {
            return "'" + text;
        }
        return text;
    }

    function collectTableData(table) {
        var columns = getExportColumns(table);
        var bodyRows = Array.prototype.slice.call(table.tBodies[0].rows || []);
        var footerRows = table.tFoot ? Array.prototype.slice.call(table.tFoot.rows || []) : [];
        var rows = bodyRows.concat(footerRows).filter(isElementVisible).map(function (row) {
            var expandedCells = expandRowCells(row);
            return columns.map(function (column) {
                return protectSpreadsheetValue(expandedCells[column.index] || "");
            });
        });

        return {
            headers: columns.map(function (column) {
                return protectSpreadsheetValue(column.label);
            }),
            rows: rows
        };
    }

    function csvEscape(value) {
        var text = value == null ? "" : String(value);
        if (/[",\r\n]/.test(text)) {
            return '"' + text.replace(/"/g, '""') + '"';
        }
        return text;
    }

    function buildCsv(data) {
        var lines = [data.headers.map(csvEscape).join(",")];
        data.rows.forEach(function (row) {
            lines.push(row.map(csvEscape).join(","));
        });
        return "\uFEFF" + lines.join("\r\n");
    }

    function htmlEscape(value) {
        return String(value == null ? "" : value)
            .replace(/&/g, "&amp;")
            .replace(/</g, "&lt;")
            .replace(/>/g, "&gt;")
            .replace(/"/g, "&quot;");
    }

    function buildXls(data) {
        var headerHtml = data.headers.map(function (header) {
            return "<th>" + htmlEscape(header) + "</th>";
        }).join("");
        var bodyHtml = data.rows.map(function (row) {
            return "<tr>" + row.map(function (value) {
                return "<td>" + htmlEscape(value) + "</td>";
            }).join("") + "</tr>";
        }).join("");

        return "\uFEFF" +
            "<html><head><meta charset=\"utf-8\"></head><body>" +
            "<table><thead><tr>" + headerHtml + "</tr></thead><tbody>" +
            bodyHtml +
            "</tbody></table></body></html>";
    }

    function slugify(value) {
        var slug = normalizeText(value)
            .toLowerCase()
            .replace(/[^a-z0-9]+/g, "-")
            .replace(/^-+|-+$/g, "");
        return slug || "table";
    }

    function filenameFor(table, extension) {
        var pageTitle = document.querySelector("h1, h2")?.textContent || document.title || "table";
        var tableName = table.dataset.exportName || table.id || pageTitle;
        return slugify(tableName) + "-" + new Date().toISOString().slice(0, 10) + "." + extension;
    }

    function download(content, mimeType, filename) {
        var blob = new Blob([content], { type: mimeType });
        var url = window.URL.createObjectURL(blob);
        var link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        window.setTimeout(function () {
            window.URL.revokeObjectURL(url);
        }, 0);
    }

    function exportTable(table, format) {
        var data = collectTableData(table);
        if (!data.headers.length || !data.rows.length) {
            window.alert("There are no visible rows to export.");
            return;
        }

        if (format === "xls") {
            download(
                buildXls(data),
                "application/vnd.ms-excel;charset=utf-8",
                filenameFor(table, "xls")
            );
            return;
        }

        download(
            buildCsv(data),
            "text/csv;charset=utf-8",
            filenameFor(table, "csv")
        );
    }

    function createExportToolbar(table) {
        var toolbar = document.createElement("div");
        toolbar.className = "table-export-toolbar d-flex justify-content-end gap-2 mb-2";
        toolbar.dataset.tableExportToolbar = "true";

        var dropdown = document.createElement("div");
        dropdown.className = "dropdown";

        var button = document.createElement("button");
        button.className = "btn btn-outline-secondary btn-sm dropdown-toggle";
        button.type = "button";
        button.setAttribute("data-bs-toggle", "dropdown");
        button.setAttribute("aria-expanded", "false");
        button.textContent = "Export";

        var menu = document.createElement("ul");
        menu.className = "dropdown-menu dropdown-menu-end";

        [
            ["csv", "CSV"],
            ["xls", "Excel (.xls)"]
        ].forEach(function (entry) {
            var item = document.createElement("li");
            var action = document.createElement("button");
            action.type = "button";
            action.className = "dropdown-item";
            action.textContent = entry[1];
            action.addEventListener("click", function () {
                exportTable(table, entry[0]);
            });
            item.appendChild(action);
            menu.appendChild(item);
        });

        dropdown.appendChild(button);
        dropdown.appendChild(menu);
        toolbar.appendChild(dropdown);
        return toolbar;
    }

    function insertToolbar(table) {
        if (table.dataset.exportInitialized === "true") {
            return;
        }
        var wrapper = table.closest(".table-responsive") || table;
        if (wrapper.previousElementSibling?.dataset?.tableExportToolbar === "true") {
            table.dataset.exportInitialized = "true";
            return;
        }
        wrapper.parentNode.insertBefore(createExportToolbar(table), wrapper);
        table.dataset.exportInitialized = "true";
    }

    function init(root) {
        var context = root || document;
        Array.prototype.slice.call(context.querySelectorAll("table")).forEach(function (table) {
            if (isExportableTable(table)) {
                insertToolbar(table);
            }
        });
    }

    window.TableExport = window.TableExport || {};
    window.TableExport.init = init;
    window.TableExport.exportTable = exportTable;

    document.addEventListener("DOMContentLoaded", function () {
        init(document);
    });
}(window, document));
