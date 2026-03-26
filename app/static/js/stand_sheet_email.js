(function () {
    document.addEventListener('DOMContentLoaded', function () {
        const modalElement = document.getElementById('emailStandSheetModal');
        if (!modalElement) {
            return;
        }

        const modal = new bootstrap.Modal(modalElement);
        const form = modalElement.querySelector('#emailStandSheetForm');
        const emailInput = form ? form.querySelector('input[name="email"]') : null;
        const invalidFeedback = emailInput ? emailInput.nextElementSibling : null;
        const alertContainer = modalElement.querySelector('[data-role="form-errors"]');
        const targetLabel = modalElement.querySelector('[data-role="stand-sheet-target"]');
        const submitButton = modalElement.querySelector('button[type="submit"]');
        const csrfField = form ? form.querySelector('input[name="csrf_token"]') : null;
        const locationIdsField = form ? form.querySelector('[data-role="location-ids"]') : null;

        let currentAction = null;
        let successMessage = 'Stand sheet email sent.';
        let currentLocationIds = [];

        function getCheckedLocationIds() {
            return Array.prototype.slice.call(
                document.querySelectorAll('#locations-table input[name="location_ids"]:checked')
            )
                .map(function (input) { return input.value; })
                .filter(Boolean);
        }

        function resetErrors() {
            if (alertContainer) {
                alertContainer.classList.add('d-none');
                alertContainer.textContent = '';
            }
            if (emailInput) {
                emailInput.classList.remove('is-invalid');
            }
            if (invalidFeedback) {
                invalidFeedback.textContent = '';
            }
        }

        function handleTrigger(event) {
            const trigger = event.target.closest('[data-email-stand-sheet]');
            if (!trigger) {
                return;
            }
            event.preventDefault();

            if (!form || !emailInput) {
                return;
            }

            const action = trigger.getAttribute('data-email-action');
            if (!action) {
                return;
            }

            currentAction = action;
            const rawIds = trigger.getAttribute('data-location-ids') || '';
            const locationIds = rawIds
                .split(',')
                .map(function (value) { return value.trim(); })
                .filter(Boolean);

            if (!locationIds.length) {
                Array.prototype.push.apply(locationIds, getCheckedLocationIds());
            }
            currentLocationIds = locationIds;

            const label = trigger.getAttribute('data-email-label') || (locationIds.length > 1
                ? `${locationIds.length} locations`
                : locationIds.length === 1 ? 'this location' : 'stand sheets');
            const defaultEmail = trigger.getAttribute('data-email-default') || '';
            successMessage = trigger.getAttribute('data-email-success') || 'Stand sheet email sent.';

            resetErrors();

            if (targetLabel) {
                targetLabel.textContent = label;
            }

            if (locationIdsField) {
                locationIdsField.value = locationIds.join(',');
            }

            emailInput.value = defaultEmail;
            setTimeout(function () {
                emailInput.focus();
            }, 150);

            modal.show();
        }

        function processErrors(payload) {
            resetErrors();
            if (!payload) {
                return;
            }

            const fieldErrors = payload.errors || payload.field_errors;
            const messages = [];

            if (fieldErrors && typeof fieldErrors === 'object') {
                const emailErrors = fieldErrors.email || fieldErrors['email'];
                if (emailErrors && emailInput) {
                    emailInput.classList.add('is-invalid');
                    const messageText = Array.isArray(emailErrors)
                        ? emailErrors.join(' ')
                        : String(emailErrors);
                    if (invalidFeedback) {
                        invalidFeedback.textContent = messageText;
                    }
                }

                Object.keys(fieldErrors).forEach(function (key) {
                    if (key === 'email') {
                        return;
                    }
                    const value = fieldErrors[key];
                    if (Array.isArray(value)) {
                        messages.push(value.join(' '));
                    } else if (value) {
                        messages.push(String(value));
                    }
                });
            }

            if (payload.message) {
                messages.push(payload.message);
            }

            if (messages.length && alertContainer) {
                alertContainer.textContent = messages.join(' ');
                alertContainer.classList.remove('d-none');
            }
        }

        function handleSubmit(event) {
            if (!form || !emailInput) {
                return;
            }
            event.preventDefault();
            resetErrors();

            if (!currentAction) {
                return;
            }

            const emailValue = emailInput.value.trim();
            if (!emailValue) {
                emailInput.classList.add('is-invalid');
                if (invalidFeedback) {
                    invalidFeedback.textContent = 'Please enter an email address.';
                }
                return;
            }

            if (!currentLocationIds.length) {
                currentLocationIds = getCheckedLocationIds();
            }

            if (locationIdsField) {
                locationIdsField.value = currentLocationIds.join(',');
            }

            const formData = new FormData(form);
            formData.set('email', emailValue);
            if (currentLocationIds.length === 0 && formData.get('location_ids')) {
                currentLocationIds = String(formData.get('location_ids'))
                    .split(',')
                    .map(function (value) { return value.trim(); })
                    .filter(Boolean);
            }

            if (submitButton) {
                submitButton.disabled = true;
            }

            const headers = { 'X-Requested-With': 'XMLHttpRequest' };
            if (csrfField) {
                headers['X-CSRFToken'] = csrfField.value;
            }

            fetch(currentAction, {
                method: 'POST',
                headers: headers,
                body: formData,
            })
                .then(function (response) {
                    const contentType = response.headers.get('Content-Type') || '';
                    if (contentType.includes('application/json')) {
                        return response.json().then(function (data) {
                            return { ok: response.ok, data: data };
                        });
                    }
                    return response.text().then(function (text) {
                        return { ok: response.ok, data: { message: text } };
                    });
                })
                .then(function (result) {
                    if (!result) {
                        return;
                    }
                    if (result.ok && result.data && (result.data.success || result.data.sent)) {
                        modal.hide();
                        const message = result.data.message || successMessage;
                        setTimeout(function () {
                            alert(message);
                        }, 150);
                    } else {
                        processErrors(result.data || {});
                    }
                })
                .catch(function () {
                    processErrors({ message: 'Unable to send stand sheet email. Please try again.' });
                })
                .finally(function () {
                    if (submitButton) {
                        submitButton.disabled = false;
                    }
                });
        }

        function handleHidden() {
            resetErrors();
            currentAction = null;
            currentLocationIds = [];
            if (emailInput) {
                emailInput.value = '';
            }
            if (locationIdsField) {
                locationIdsField.value = '';
            }
        }

        document.addEventListener('click', handleTrigger);
        if (form) {
            form.addEventListener('submit', handleSubmit);
        }
        modalElement.addEventListener('hidden.bs.modal', handleHidden);
    });
})();
