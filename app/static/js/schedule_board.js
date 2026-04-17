document.addEventListener("DOMContentLoaded", () => {
  const filterForm = document.querySelector("[data-schedule-filter-form]");
  if (filterForm) {
    filterForm
      .querySelectorAll("[data-auto-submit-filter]")
      .forEach((input) => {
        input.addEventListener("change", () => filterForm.requestSubmit());
      });
  }

  const modal = document.getElementById("scheduleShiftModal");
  if (!modal) {
    return;
  }

  const inputIds = {
    shiftId: modal.dataset.shiftIdInput,
    shiftDate: modal.dataset.shiftDateInput,
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

  let eventLocationMap = {};
  try {
    eventLocationMap = JSON.parse(modal.dataset.eventLocationMap || "{}");
  } catch (_error) {
    eventLocationMap = {};
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
    if (inputs.assignedUser) {
      inputs.assignedUser.value = trigger.dataset.assignedUserId || "0";
    }
    if (inputs.position) {
      inputs.position.value = trigger.dataset.positionId || "";
    }
    if (inputs.assignmentMode) {
      inputs.assignmentMode.value = trigger.dataset.assignmentMode || "assigned";
    }
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
  syncPaidHoursState();
});
