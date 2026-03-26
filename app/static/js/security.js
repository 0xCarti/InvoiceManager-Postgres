document.addEventListener('DOMContentLoaded', () => {
  document.addEventListener(
    'click',
    (event) => {
      const confirmTarget = event.target.closest('[data-confirm-message]');
      if (confirmTarget) {
        const message = confirmTarget.getAttribute('data-confirm-message') || 'Are you sure?';
        if (!window.confirm(message)) {
          event.preventDefault();
          event.stopImmediatePropagation();
          return;
        }
      }

      const actionTarget = event.target.closest('[data-action]');
      if (!actionTarget) {
        return;
      }

      const action = actionTarget.getAttribute('data-action');
      switch (action) {
        case 'reload':
          event.preventDefault();
          window.location.reload();
          break;
        case 'print':
          event.preventDefault();
          window.print();
          break;
        default:
          break;
      }
    },
    true
  );

  document.addEventListener('change', (event) => {
    const field = event.target.closest('.js-auto-submit');
    if (field && field.form) {
      field.form.submit();
    }
  });
});
