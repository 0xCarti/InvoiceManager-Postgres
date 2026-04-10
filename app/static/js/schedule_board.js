document.addEventListener("DOMContentLoaded", () => {
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
  };

  const inputs = Object.fromEntries(
    Object.entries(inputIds).map(([key, id]) => [key, document.getElementById(id)]),
  );
  const title = document.getElementById("scheduleShiftModalTitle");
  const repeatBoxes = Array.from(
    modal.querySelectorAll('input[name$="repeat_days"]'),
  );

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

  function resetRepeatBoxes() {
    repeatBoxes.forEach((box) => {
      box.checked = false;
    });
  }

  function setDefaultRepeat(shiftDate) {
    if (!shiftDate) {
      return;
    }
    const date = new Date(`${shiftDate}T00:00:00`);
    const weekday = (date.getDay() + 6) % 7;
    repeatBoxes.forEach((box) => {
      box.checked = Number(box.value) === weekday;
    });
  }

  function populateFromTrigger(trigger) {
    const isEdit = Boolean(trigger.dataset.shiftId);
    title.textContent = isEdit ? "Edit Shift" : "Add Shift";
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
    if (inputs.location) {
      inputs.location.value = trigger.dataset.locationId || "0";
    }
    if (inputs.event) {
      inputs.event.value = trigger.dataset.eventId || "0";
    }
    if (inputs.notes) {
      inputs.notes.value = trigger.dataset.notes || "";
    }
    if (inputs.color) {
      inputs.color.value = trigger.dataset.color || "";
    }
    setCheckboxValue(inputs.isLocked, trigger.dataset.isLocked);

    resetRepeatBoxes();
    if (!isEdit) {
      setDefaultRepeat(trigger.dataset.shiftDate);
    }
    updateAssignedUserState();
  }

  document.querySelectorAll("[data-open-shift-modal]").forEach((trigger) => {
    trigger.addEventListener("click", () => populateFromTrigger(trigger));
  });

  if (inputs.assignmentMode) {
    inputs.assignmentMode.addEventListener("change", updateAssignedUserState);
  }
});
