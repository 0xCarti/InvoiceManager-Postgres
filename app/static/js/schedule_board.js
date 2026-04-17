document.addEventListener("DOMContentLoaded", () => {
  const filterForm = document.querySelector("[data-schedule-filter-form]");
  if (filterForm) {
    filterForm
      .querySelectorAll("[data-auto-submit-filter]")
      .forEach((input) => {
        input.addEventListener("change", () => filterForm.requestSubmit());
      });
  }

  function getCookie(name) {
    const cookieString = document.cookie;
    if (!cookieString) {
      return null;
    }
    const cookies = cookieString.split(";");
    for (const cookie of cookies) {
      const [rawKey, ...rawValue] = cookie.trim().split("=");
      if (rawKey === name) {
        return decodeURIComponent(rawValue.join("="));
      }
    }
    return null;
  }

  function findCsrfToken() {
    const buttonToken =
      saveDefaultButton?.dataset.scheduleDefaultCsrf?.trim() || "";
    if (buttonToken) {
      return buttonToken;
    }

    const pageField = document.querySelector('input[name="csrf_token"]');
    if (pageField?.value) {
      return pageField.value;
    }

    const metaToken = document
      .querySelector('meta[name="csrf-token"]')
      ?.getAttribute("content");
    if (metaToken) {
      return metaToken;
    }

    return getCookie("csrf_token");
  }

  const saveDefaultButton = document.querySelector("[data-schedule-save-default]");
  const saveDefaultFeedback = document.querySelector("[data-schedule-default-feedback]");

  function renderDefaultFeedback(message, isError = false) {
    if (!saveDefaultFeedback) {
      return;
    }
    saveDefaultFeedback.textContent = message;
    if (!message) {
      saveDefaultFeedback.className = "schedule-default-feedback small text-muted";
      return;
    }
    saveDefaultFeedback.className = `schedule-default-feedback small ${isError ? "text-danger" : "text-success"}`;
  }

  if (saveDefaultButton && filterForm) {
    saveDefaultButton.addEventListener("click", async () => {
      const departmentInput = filterForm.querySelector('[name="department_id"]');
      const scope = saveDefaultButton.dataset.scheduleDefaultScope || "";
      if (!departmentInput || !scope) {
        renderDefaultFeedback("Unable to save the default department.", true);
        return;
      }

      saveDefaultButton.disabled = true;
      renderDefaultFeedback("");

      try {
        const formData = new FormData();
        formData.set("scope", scope);
        formData.set("department_id", departmentInput.value || "all");

        const headers = new Headers();
        const csrfToken = findCsrfToken();
        if (csrfToken) {
          headers.set("X-CSRFToken", csrfToken);
        }

        const response = await fetch("/preferences/filters", {
          method: "POST",
          body: formData,
          headers,
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          renderDefaultFeedback(
            payload.error || "Unable to save the default department.",
            true,
          );
          return;
        }
        renderDefaultFeedback("Default department saved.");
      } catch (error) {
        window.console.error("Failed to save schedule department default.", error);
        renderDefaultFeedback("Unable to save the default department.", true);
      } finally {
        saveDefaultButton.disabled = false;
      }
    });
  }

  const modal = document.getElementById("scheduleShiftModal");
  if (!modal) {
    return;
  }

  const inputIds = {
    shiftId: modal.dataset.shiftIdInput,
    shiftDate: modal.dataset.shiftDateInput,
    department: modal.dataset.departmentInput,
    assignedUser: modal.dataset.assignedUserInput,
    position: modal.dataset.positionInput,
    assignmentMode: modal.dataset.assignmentModeInput,
    startTime: modal.dataset.startTimeInput,
    endTime: modal.dataset.endTimeInput,
    paidHours: modal.dataset.paidHoursInput,
    paidHoursManual: modal.dataset.paidHoursManualInput,
    location: modal.dataset.locationInput,
    event: modal.dataset.eventInput,
    notes: modal.dataset.notesInput,
    color: modal.dataset.colorInput,
    isLocked: modal.dataset.isLockedInput,
    copyCount: modal.dataset.copyCountInput,
    repeatWeeks: modal.dataset.repeatWeeksInput,
  };

  const inputs = Object.fromEntries(
    Object.entries(inputIds).map(([key, id]) => [key, document.getElementById(id)]),
  );
  const title = document.getElementById("scheduleShiftModalTitle");
  const shiftDateSection = modal.querySelector("[data-shift-date-section]");
  const createOptionsSection = modal.querySelector("[data-shift-create-options]");
  const targetDayBoxes = Array.from(
    modal.querySelectorAll('[data-target-day-checkbox="1"]'),
  );
  const originalLocationOptions = inputs.location
    ? Array.from(inputs.location.options).map((option) => ({
        value: option.value,
        label: option.textContent,
      }))
    : [];
  const originalPositionOptions = inputs.position
    ? Array.from(inputs.position.options).map((option) => ({
        value: option.value,
        label: option.textContent,
      }))
    : [];

  let eventLocationMap = {};
  let departmentPositionMap = {};
  let userPositionMapByDepartment = {};
  try {
    eventLocationMap = JSON.parse(modal.dataset.eventLocationMap || "{}");
  } catch (_error) {
    eventLocationMap = {};
  }
  try {
    departmentPositionMap = JSON.parse(modal.dataset.departmentPositionMap || "{}");
  } catch (_error) {
    departmentPositionMap = {};
  }
  try {
    userPositionMapByDepartment = JSON.parse(
      modal.dataset.userPositionMapByDepartment || "{}",
    );
  } catch (_error) {
    userPositionMapByDepartment = {};
  }

  function updateAssignedUserState() {
    const mode = inputs.assignmentMode?.value || "assigned";
    const disabled = mode !== "assigned";
    if (inputs.assignedUser) {
      inputs.assignedUser.disabled = disabled;
      if (disabled) {
        inputs.assignedUser.value = "0";
      }
    }
    syncPositionOptions(inputs.position?.value || "");
  }

  function getDepartmentPositionOptions(departmentId) {
    if (!departmentId || departmentId === "0") {
      return [];
    }
    const options = Array.isArray(departmentPositionMap[departmentId])
      ? departmentPositionMap[departmentId].map((option) => ({
          value: String(option.value),
          label: option.label,
        }))
      : [];
    return options.length ? options : originalPositionOptions;
  }

  function setCheckboxValue(input, rawValue) {
    if (!input) {
      return;
    }
    input.checked = rawValue === "1" || rawValue === "true" || rawValue === true;
  }

  function parseTimeToMinutes(value) {
    if (!value || !/^\d{2}:\d{2}$/.test(value)) {
      return null;
    }
    const [hours, minutes] = value.split(":").map(Number);
    return (hours * 60) + minutes;
  }

  function calculatePaidHours() {
    const startMinutes = parseTimeToMinutes(inputs.startTime?.value);
    const endMinutes = parseTimeToMinutes(inputs.endTime?.value);
    if (startMinutes === null || endMinutes === null || endMinutes <= startMinutes) {
      return "";
    }
    return ((endMinutes - startMinutes) / 60).toFixed(2);
  }

  function syncPaidHoursState() {
    const isManual = Boolean(inputs.paidHoursManual?.checked);
    if (inputs.paidHours) {
      inputs.paidHours.readOnly = !isManual;
      inputs.paidHours.classList.toggle("bg-body-secondary", !isManual);
      if (!isManual) {
        inputs.paidHours.value = calculatePaidHours();
      }
    }
  }

  function resetTargetDayBoxes() {
    targetDayBoxes.forEach((box) => {
      box.checked = false;
    });
  }

  function syncShiftDateFromTargetDays() {
    if (!inputs.shiftDate) {
      return;
    }
    const firstChecked = targetDayBoxes.find((box) => box.checked);
    if (firstChecked?.dataset.targetDate) {
      inputs.shiftDate.value = firstChecked.dataset.targetDate;
    }
  }

  function setDefaultTargetDay(shiftDate) {
    if (!shiftDate) {
      return;
    }
    targetDayBoxes.forEach((box) => {
      box.checked = box.dataset.targetDate === shiftDate;
    });
    syncShiftDateFromTargetDays();
  }

  function syncCreateMode(isEdit) {
    if (title) {
      title.textContent = isEdit ? "Edit Shift" : "Add Shift";
    }
    if (shiftDateSection) {
      shiftDateSection.hidden = !isEdit;
    }
    if (createOptionsSection) {
      createOptionsSection.hidden = isEdit;
    }
    if (inputs.copyCount) {
      inputs.copyCount.disabled = isEdit;
      if (!isEdit && !inputs.copyCount.value) {
        inputs.copyCount.value = "1";
      }
      if (isEdit) {
        inputs.copyCount.value = "1";
      }
    }
    if (inputs.repeatWeeks) {
      inputs.repeatWeeks.disabled = isEdit;
      if (!isEdit && !inputs.repeatWeeks.value) {
        inputs.repeatWeeks.value = "0";
      }
      if (isEdit) {
        inputs.repeatWeeks.value = "0";
      }
    }
  }

  function replaceLocationOptions(options, preferredValue = "0") {
    if (!inputs.location) {
      return;
    }
    inputs.location.innerHTML = "";
    options.forEach((option) => {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      inputs.location.appendChild(element);
    });
    const preferred = String(preferredValue || "0");
    const hasPreferred = Array.from(inputs.location.options).some(
      (option) => option.value === preferred,
    );
    inputs.location.value = hasPreferred ? preferred : "0";
  }

  function replacePositionOptions(options, preferredValue = "") {
    if (!inputs.position) {
      return;
    }
    inputs.position.innerHTML = "";
    options.forEach((option) => {
      const element = document.createElement("option");
      element.value = option.value;
      element.textContent = option.label;
      inputs.position.appendChild(element);
    });
    const preferred = String(preferredValue || "");
    const hasPreferred = Array.from(inputs.position.options).some(
      (option) => option.value === preferred,
    );
    if (hasPreferred) {
      inputs.position.value = preferred;
    } else if (inputs.position.options.length > 0) {
      inputs.position.value = inputs.position.options[0].value;
    } else {
      inputs.position.value = "";
    }
  }

  function syncPositionOptions(preferredValue = null) {
    if (!inputs.position) {
      return;
    }
    const currentPreferred = String(preferredValue ?? inputs.position.value ?? "");
    const departmentId = String(inputs.department?.value || "0");
    const mode = inputs.assignmentMode?.value || "assigned";
    const assignedUserId = String(inputs.assignedUser?.value || "0");
    const departmentOptions = getDepartmentPositionOptions(departmentId);

    if (!departmentOptions.length) {
      inputs.position.disabled = true;
      replacePositionOptions(
        [{ value: "", label: departmentId === "0" ? "Select a department first" : "No active positions" }],
        "",
      );
      return;
    }

    if (mode !== "assigned" || assignedUserId === "0") {
      inputs.position.disabled = false;
      replacePositionOptions(departmentOptions, currentPreferred);
      return;
    }

    const eligiblePositionIds = new Set(
      (
        Array.isArray(
          (userPositionMapByDepartment[departmentId] || {})[assignedUserId],
        )
          ? (userPositionMapByDepartment[departmentId] || {})[assignedUserId]
          : []
      )
        .map((value) => String(value)),
    );
    let eligibleOptions = departmentOptions.filter((option) =>
      eligiblePositionIds.has(String(option.value)),
    );
    if (
      currentPreferred
      && !eligiblePositionIds.has(currentPreferred)
    ) {
      const currentOption = departmentOptions.find(
        (option) => String(option.value) === currentPreferred,
      );
      if (currentOption) {
        eligibleOptions = [currentOption, ...eligibleOptions];
      }
    }

    if (!eligibleOptions.length) {
      inputs.position.disabled = true;
      replacePositionOptions(
        [{ value: "", label: "No eligible positions" }],
        "",
      );
      return;
    }

    inputs.position.disabled = false;
    replacePositionOptions(eligibleOptions, currentPreferred);
  }

  function syncLocationOptions(preferredValue = null) {
    if (!inputs.location || !inputs.event) {
      return;
    }
    const currentPreferred = preferredValue ?? inputs.location.value ?? "0";
    const eventId = String(inputs.event.value || "0");
    if (eventId === "0") {
      replaceLocationOptions(originalLocationOptions, currentPreferred);
      return;
    }
    const eventLocations = Array.isArray(eventLocationMap[eventId])
      ? eventLocationMap[eventId]
      : [];
    const noLocationOption = originalLocationOptions.find(
      (option) => option.value === "0",
    ) || { value: "0", label: "No location" };
    const narrowedOptions = [
      noLocationOption,
      ...eventLocations.map((location) => ({
        value: String(location.id),
        label: location.name,
      })),
    ];
    replaceLocationOptions(narrowedOptions, currentPreferred);
  }

  function populateFromTrigger(trigger) {
    const isEdit = Boolean(trigger.dataset.shiftId);
    syncCreateMode(isEdit);
    if (inputs.shiftId) {
      inputs.shiftId.value = trigger.dataset.shiftId || "";
    }
    if (inputs.shiftDate) {
      inputs.shiftDate.value = trigger.dataset.shiftDate || "";
    }
    if (inputs.department) {
      inputs.department.value =
        trigger.dataset.departmentId
        || inputs.department.value
        || (inputs.department.options[0]?.value ?? "0");
    }
    if (inputs.assignedUser) {
      inputs.assignedUser.value = trigger.dataset.assignedUserId || "0";
    }
    if (inputs.assignmentMode) {
      inputs.assignmentMode.value = trigger.dataset.assignmentMode || "assigned";
    }
    syncPositionOptions(trigger.dataset.positionId || "");
    if (inputs.startTime) {
      inputs.startTime.value = trigger.dataset.startTime || "";
    }
    if (inputs.endTime) {
      inputs.endTime.value = trigger.dataset.endTime || "";
    }
    if (inputs.paidHours) {
      inputs.paidHours.value = trigger.dataset.paidHours || "";
    }
    setCheckboxValue(inputs.paidHoursManual, trigger.dataset.paidHoursManual);
    if (inputs.event) {
      inputs.event.value = trigger.dataset.eventId || "0";
    }
    syncLocationOptions(trigger.dataset.locationId || "0");
    if (inputs.notes) {
      inputs.notes.value = trigger.dataset.notes || "";
    }
    if (inputs.color) {
      inputs.color.value = trigger.dataset.color || "";
    }
    setCheckboxValue(inputs.isLocked, trigger.dataset.isLocked);

    resetTargetDayBoxes();
    if (!isEdit) {
      setDefaultTargetDay(trigger.dataset.shiftDate);
      if (inputs.copyCount) {
        inputs.copyCount.value = "1";
      }
      if (inputs.repeatWeeks) {
        inputs.repeatWeeks.value = "0";
      }
    }
    updateAssignedUserState();
    syncPaidHoursState();
  }

  document.querySelectorAll("[data-open-shift-modal]").forEach((trigger) => {
    trigger.addEventListener("click", () => populateFromTrigger(trigger));
  });

  if (inputs.assignmentMode) {
    inputs.assignmentMode.addEventListener("change", updateAssignedUserState);
  }
  if (inputs.department) {
    inputs.department.addEventListener("change", () => {
      syncPositionOptions("");
    });
  }
  if (inputs.assignedUser) {
    inputs.assignedUser.addEventListener("change", () => {
      syncPositionOptions(inputs.position?.value || "");
    });
  }
  if (inputs.startTime) {
    inputs.startTime.addEventListener("input", syncPaidHoursState);
    inputs.startTime.addEventListener("change", syncPaidHoursState);
  }
  if (inputs.endTime) {
    inputs.endTime.addEventListener("input", syncPaidHoursState);
    inputs.endTime.addEventListener("change", syncPaidHoursState);
  }
  if (inputs.paidHoursManual) {
    inputs.paidHoursManual.addEventListener("change", syncPaidHoursState);
  }
  if (inputs.event) {
    inputs.event.addEventListener("change", () => syncLocationOptions("0"));
  }
  targetDayBoxes.forEach((box) => {
    box.addEventListener("change", syncShiftDateFromTargetDays);
  });

  syncLocationOptions(inputs.location?.value || "0");
  syncPositionOptions(inputs.position?.value || "");
  syncPaidHoursState();
});
