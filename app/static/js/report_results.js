(function (window, document) {
    "use strict";

    function isReportPage() {
        return document.body.classList.contains("report-page");
    }

    function contentRoot() {
        return document.querySelector("body > .container.mt-3") || document.body;
    }

    function isReportFilterForm(form) {
        if (!form || form.closest("nav, .navbar, .offcanvas, .modal")) {
            return false;
        }
        if (form.dataset.reportSameTab === "true") {
            return false;
        }
        if (!form.querySelector("button[type='submit'], input[type='submit']")) {
            return false;
        }
        return contentRoot().contains(form);
    }

    function ensureResultFlag(form) {
        var input = form.querySelector("input[name='_report_result']");
        if (!input) {
            input = document.createElement("input");
            input.type = "hidden";
            input.name = "_report_result";
            form.appendChild(input);
        }
        input.value = "1";
    }

    function prepareReportForms() {
        Array.prototype.slice.call(contentRoot().querySelectorAll("form")).forEach(function (form) {
            if (!isReportFilterForm(form)) {
                return;
            }
            form.target = "_blank";
            form.rel = "noopener";
            form.dataset.reportFilterForm = "true";
            ensureResultFlag(form);
        });
    }

    function hasGeneratedReportOutput() {
        var root = contentRoot();
        if (root.querySelector("table")) {
            return true;
        }
        return Array.prototype.slice.call(root.querySelectorAll("h3, h4, .alert-info")).some(function (node) {
            return /report results|batch results|results|no .*matched|no .*found/i.test(node.textContent || "");
        });
    }

    function hideFiltersOnResultTab() {
        if (!document.body.classList.contains("report-result-tab")) {
            return;
        }
        if (!hasGeneratedReportOutput()) {
            return;
        }
        Array.prototype.slice.call(contentRoot().querySelectorAll("form[data-report-filter-form='true']")).forEach(function (form) {
            form.hidden = true;
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        if (!isReportPage()) {
            return;
        }
        prepareReportForms();
        hideFiltersOnResultTab();
        if (window.TableExport && typeof window.TableExport.init === "function") {
            window.TableExport.init(contentRoot());
        }
    });
}(window, document));
