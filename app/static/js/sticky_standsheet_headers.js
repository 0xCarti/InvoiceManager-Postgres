(function () {
    function updateStickyHeaderOffsets(table) {
        if (!table || !table.tHead) {
            return;
        }
        let offset = 0;
        Array.from(table.tHead.rows).forEach(function (row) {
            Array.from(row.cells).forEach(function (cell) {
                cell.style.setProperty("--standsheet-sticky-top", offset + "px");
            });
            offset += row.getBoundingClientRect().height;
        });
    }

    function initializeStickyHeader(table) {
        if (!table || table.dataset.stickyStandsheetInitialized === "1") {
            return;
        }
        table.dataset.stickyStandsheetInitialized = "1";

        var syncOffsets = function () {
            window.requestAnimationFrame(function () {
                updateStickyHeaderOffsets(table);
            });
        };

        syncOffsets();
        window.addEventListener("resize", syncOffsets);

        if ("ResizeObserver" in window) {
            var observer = new ResizeObserver(syncOffsets);
            observer.observe(table);
            if (table.tHead) {
                observer.observe(table.tHead);
                Array.from(table.tHead.rows).forEach(function (row) {
                    observer.observe(row);
                });
            }
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        document
            .querySelectorAll("[data-sticky-standsheet-header]")
            .forEach(initializeStickyHeader);
    });
})();
