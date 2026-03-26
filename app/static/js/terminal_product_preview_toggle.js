(function () {
    function resolvePanel(button) {
        if (!button) {
            return null;
        }
        var targetSelector = button.getAttribute('data-target');
        if (targetSelector) {
            var target = document.querySelector(targetSelector);
            if (target) {
                return target;
            }
        }
        var controls = button.getAttribute('aria-controls');
        if (controls) {
            var byId = document.getElementById(controls);
            if (byId) {
                return byId;
            }
        }
        return document.querySelector('[data-role="product-mapping-preview"]');
    }

    function initToggle(button) {
        var panel = resolvePanel(button);
        if (!panel) {
            return;
        }
        var showText = button.getAttribute('data-show-text') || 'Show matched products';
        var hideText = button.getAttribute('data-hide-text') || 'Hide matched products';

        function updateState() {
            var isHidden = panel.classList.contains('d-none');
            button.setAttribute('aria-expanded', (!isHidden).toString());
            button.textContent = isHidden ? showText : hideText;
        }

        button.addEventListener('click', function () {
            panel.classList.toggle('d-none');
            updateState();
        });

        updateState();
    }

    function init() {
        var buttons = document.querySelectorAll('[data-role="toggle-product-preview"]');
        if (!buttons.length) {
            return;
        }
        buttons.forEach(initToggle);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
