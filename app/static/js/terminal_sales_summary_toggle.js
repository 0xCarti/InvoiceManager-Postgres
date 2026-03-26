(function () {
    function initToggle() {
        const toggleButton = document.querySelector('[data-role="toggle-sales-summary"]');
        const summaryPanel = document.querySelector('[data-role="sales-summary-panel"]');
        if (!toggleButton || !summaryPanel) {
            return;
        }

        const showText = toggleButton.getAttribute('data-show-text') || 'Show sales summary';
        const hideText = toggleButton.getAttribute('data-hide-text') || 'Hide sales summary';

        function updateState() {
            const isHidden = summaryPanel.classList.contains('d-none');
            toggleButton.setAttribute('aria-expanded', (!isHidden).toString());
            toggleButton.textContent = isHidden ? showText : hideText;
        }

        toggleButton.addEventListener('click', function () {
            summaryPanel.classList.toggle('d-none');
            updateState();
        });

        updateState();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initToggle);
    } else {
        initToggle();
    }
})();
