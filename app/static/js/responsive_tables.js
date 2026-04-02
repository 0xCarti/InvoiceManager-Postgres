document.addEventListener("DOMContentLoaded", () => {
    const normalizeLabel = (text) => (text || "").replace(/\s+/g, " ").trim();

    document.querySelectorAll("table.table-mobile-card").forEach((table) => {
        const headerCells = Array.from(table.querySelectorAll("thead th"));
        const labels = headerCells.map((cell) => normalizeLabel(cell.textContent));

        table.querySelectorAll("tbody tr").forEach((row) => {
            Array.from(row.children).forEach((cell, index) => {
                if (!["TD", "TH"].includes(cell.tagName)) {
                    return;
                }
                if (!cell.dataset.label) {
                    cell.dataset.label = labels[index] || "";
                }
            });
        });
    });
});
