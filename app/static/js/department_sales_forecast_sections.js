(function () {
    'use strict';

    function handleToggleActivation(toggle, target) {
        var isExpanded = toggle.getAttribute('aria-expanded') === 'true';

        if (isExpanded) {
            toggle.setAttribute('aria-expanded', 'false');
            target.classList.add('d-none');
        } else {
            toggle.setAttribute('aria-expanded', 'true');
            target.classList.remove('d-none');
        }
    }

    function setupToggle(toggle) {
        var targetId = toggle.getAttribute('data-target');
        if (!targetId) {
            return;
        }

        var target = document.getElementById(targetId);
        if (!target) {
            return;
        }

        toggle.setAttribute('aria-expanded', 'false');
        toggle.setAttribute('aria-controls', targetId);
        target.classList.add('d-none');

        toggle.addEventListener('click', function (event) {
            event.preventDefault();
            handleToggleActivation(toggle, target);
        });

        toggle.addEventListener('keydown', function (event) {
            if (event.key === 'Enter' || event.key === ' ' || event.key === 'Spacebar' || event.key === 'Space') {
                event.preventDefault();
                handleToggleActivation(toggle, target);
            }
        });
    }

    function initializeSections() {
        var toggles = document.querySelectorAll('[data-role="collapse-toggle"]');

        if (!toggles.length) {
            return;
        }

        toggles.forEach(function (toggle) {
            setupToggle(toggle);
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initializeSections);
    } else {
        initializeSections();
    }
}());
