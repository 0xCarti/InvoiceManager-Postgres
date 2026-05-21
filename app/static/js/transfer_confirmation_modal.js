(function (window, document) {
  'use strict';

  const checkHeader = 'X-Transfer-Confirmation-Check';
  const confirmedFieldName = '_transfer_confirmed';
  const legacySubmitFieldName = 'submit';

  let activeForm = null;
  let activeCancelHandler = null;
  let modalInstance = null;
  let confirmedFromModal = false;

  function getModalElement() {
    return document.getElementById('transferConfirmationModal');
  }

  function getModal() {
    const modalEl = getModalElement();
    if (!modalEl || !window.bootstrap || !window.bootstrap.Modal) {
      return null;
    }
    if (!modalInstance) {
      modalInstance = new window.bootstrap.Modal(modalEl);
    }
    return modalInstance;
  }

  function ensureSubmitMarker(form) {
    let marker = form.querySelector(`input[name="${confirmedFieldName}"][data-transfer-confirmation-marker="1"]`);
    if (!marker) {
      marker = document.createElement('input');
      marker.type = 'hidden';
      marker.name = confirmedFieldName;
      marker.value = '1';
      marker.dataset.transferConfirmationMarker = '1';
      form.appendChild(marker);
    }
    return marker;
  }

  function removeSubmitMarker(form) {
    const marker = form.querySelector(`input[name="${confirmedFieldName}"][data-transfer-confirmation-marker="1"]`);
    if (marker) {
      marker.remove();
    }
  }

  function nativeSubmit(form) {
    if (window.HTMLFormElement && window.HTMLFormElement.prototype.submit) {
      window.HTMLFormElement.prototype.submit.call(form);
      return;
    }
    form.submit();
  }

  function submitForm(form, confirmed) {
    if (confirmed) {
      ensureSubmitMarker(form);
    } else {
      removeSubmitMarker(form);
    }
    nativeSubmit(form);
  }

  function setWarnings(warnings) {
    const modalEl = getModalElement();
    if (!modalEl) {
      return;
    }
    const list = modalEl.querySelector('[data-transfer-confirmation-warnings]');
    if (!list) {
      return;
    }
    list.innerHTML = '';
    warnings.forEach(function (message) {
      const item = document.createElement('li');
      item.textContent = message;
      list.appendChild(item);
    });
  }

  function showModal(payload, form, onCancel) {
    const modalEl = getModalElement();
    const modal = getModal();
    if (!modalEl || !modal) {
      submitForm(form, false);
      return;
    }

    const title = modalEl.querySelector('#transferConfirmationModalTitle');
    const confirmButton = modalEl.querySelector('[data-transfer-confirmation-confirm]');
    if (title) {
      title.textContent = payload.title || 'Confirm Transfer';
    }
    if (confirmButton) {
      confirmButton.textContent = payload.confirm_label || 'Confirm';
    }
    setWarnings(Array.isArray(payload.warnings) ? payload.warnings : []);

    activeForm = form;
    activeCancelHandler = typeof onCancel === 'function' ? onCancel : null;
    confirmedFromModal = false;
    modal.show();
  }

  async function checkConfirmation(form) {
    const data = new FormData(form);
    data.delete(confirmedFieldName);
    data.delete(legacySubmitFieldName);
    const response = await window.fetch(form.action, {
      method: (form.method || 'POST').toUpperCase(),
      body: data,
      credentials: 'same-origin',
      headers: {
        Accept: 'application/json',
        [checkHeader]: '1',
      },
    });
    if (!response.ok) {
      throw new Error('Transfer confirmation check failed.');
    }
    return response.json();
  }

  function submitWithConfirmation(form, options) {
    const opts = options || {};
    checkConfirmation(form)
      .then(function (payload) {
        if (payload && payload.requires_confirmation) {
          showModal(payload, form, opts.onCancel);
          return;
        }
        submitForm(form, true);
      })
      .catch(function () {
        submitForm(form, false);
      });
  }

  document.addEventListener('submit', function (event) {
    const form = event.target.closest('form.js-transfer-confirm-form');
    if (!form) {
      return;
    }
    event.preventDefault();
    submitWithConfirmation(form);
  });

  document.addEventListener('click', function (event) {
    const confirmButton = event.target.closest('[data-transfer-confirmation-confirm]');
    if (!confirmButton || !activeForm) {
      return;
    }
    confirmedFromModal = true;
    const form = activeForm;
    activeForm = null;
    activeCancelHandler = null;
    const modal = getModal();
    if (modal) {
      modal.hide();
    }
    submitForm(form, true);
  });

  document.addEventListener('hidden.bs.modal', function (event) {
    if (event.target !== getModalElement()) {
      return;
    }
    if (!confirmedFromModal && activeCancelHandler) {
      activeCancelHandler();
    }
    activeForm = null;
    activeCancelHandler = null;
    confirmedFromModal = false;
  });

  window.TransferConfirmation = {
    submit: submitWithConfirmation,
  };
})(window, document);
