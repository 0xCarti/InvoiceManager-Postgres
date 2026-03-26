const FilterDefaults = (() => {
  const DEFAULT_ENDPOINT = '/preferences/filters';

  function getCookie(name) {
    const cookieString = document.cookie;
    if (!cookieString) {
      return null;
    }
    const cookies = cookieString.split(';');
    for (const cookie of cookies) {
      const [rawKey, ...rawValue] = cookie.trim().split('=');
      if (rawKey === name) {
        return decodeURIComponent(rawValue.join('='));
      }
    }
    return null;
  }

  function findFeedbackElement(modal) {
    if (!modal) {
      return null;
    }
    return modal.querySelector('[data-filter-feedback]');
  }

  function renderFeedback(modal, message, variant) {
    const feedbackEl = findFeedbackElement(modal);
    if (!feedbackEl) {
      window.console.warn('Missing filter feedback container.');
      window.alert(message);
      return;
    }

    const alertClasses = ['alert', `alert-${variant}`, 'mb-0'];
    feedbackEl.className = alertClasses.join(' ');
    feedbackEl.textContent = message;
    feedbackEl.classList.remove('d-none');
  }

  function clearFeedback(modal) {
    const feedbackEl = findFeedbackElement(modal);
    if (!feedbackEl) {
      return;
    }
    feedbackEl.className = 'alert d-none';
    feedbackEl.textContent = '';
  }

  async function handleSaveClick(event) {
    const trigger = event.target.closest('[data-filter-save]');
    if (!trigger) {
      return;
    }

    const modal = trigger.closest('[data-filter-modal]');
    const form = modal ? modal.querySelector('[data-filter-form]') : null;

    if (!form) {
      return;
    }

    event.preventDefault();

    const scope =
      form.getAttribute('data-filter-scope') ||
      (modal ? modal.getAttribute('data-filter-scope') : '') ||
      '';

    if (!scope) {
      renderFeedback(modal, 'Unable to save filters: missing scope.', 'danger');
      return;
    }

    clearFeedback(modal);

    const originalDisabled = trigger.disabled;
    trigger.disabled = true;

    try {
      const formData = new FormData(form);
      formData.set('scope', scope);

      const csrfField = form.querySelector('input[name="csrf_token"]');
      const csrfToken = csrfField ? csrfField.value : getCookie('csrf_token');

      const requestHeaders = new Headers();
      if (csrfToken) {
        requestHeaders.set('X-CSRFToken', csrfToken);
      }

      const endpoint =
        form.getAttribute('data-filter-save-url') || DEFAULT_ENDPOINT;

      const response = await fetch(endpoint, {
        method: 'POST',
        body: formData,
        headers: requestHeaders,
      });

      const contentType = response.headers.get('content-type') || '';
      const isJSON = contentType.includes('application/json');
      const payload = isJSON ? await response.json() : null;

      if (!response.ok) {
        const message =
          (payload && (payload.error || payload.message)) ||
          'Unable to save filter defaults.';
        renderFeedback(modal, message, 'danger');
        return;
      }

      const message =
        (payload && (payload.message || 'Filter defaults saved.')) ||
        'Filter defaults saved.';
      renderFeedback(modal, message, 'success');
    } catch (error) {
      window.console.error('Failed to save filter defaults.', error);
      renderFeedback(modal, 'Unable to save filter defaults.', 'danger');
    } finally {
      trigger.disabled = originalDisabled;
    }
  }

  function init() {
    document.addEventListener('click', handleSaveClick);
  }

  return {
    init,
  };
})();

document.addEventListener('DOMContentLoaded', () => {
  FilterDefaults.init();
});
