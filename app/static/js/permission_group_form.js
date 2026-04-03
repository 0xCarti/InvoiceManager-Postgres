document.addEventListener("DOMContentLoaded", () => {
  const forms = document.querySelectorAll("[data-permission-group-form]");

  forms.forEach((form) => {
    const itemInputs = Array.from(form.querySelectorAll("[data-permission-item]"));
    const categoryKeys = [...new Set(itemInputs.map((input) => input.dataset.permissionItem))];

    function getCategoryInputs(categoryKey) {
      return itemInputs.filter(
        (input) => input.dataset.permissionItem === categoryKey
      );
    }

    function updateCategoryState(categoryKey) {
      const categoryInputs = getCategoryInputs(categoryKey);
      const toggle = form.querySelector(
        `[data-permission-category-toggle="${categoryKey}"]`
      );
      const selectedCount = categoryInputs.filter((input) => input.checked).length;
      const countLabel = form.querySelector(
        `[data-permission-selected-count="${categoryKey}"]`
      );

      if (countLabel) {
        countLabel.textContent = String(selectedCount);
      }

      if (!toggle) {
        return;
      }

      toggle.checked =
        categoryInputs.length > 0 && selectedCount === categoryInputs.length;
      toggle.indeterminate =
        selectedCount > 0 && selectedCount < categoryInputs.length;
    }

    categoryKeys.forEach((categoryKey) => {
      const toggle = form.querySelector(
        `[data-permission-category-toggle="${categoryKey}"]`
      );
      const categoryInputs = getCategoryInputs(categoryKey);

      if (toggle) {
        toggle.addEventListener("change", () => {
          categoryInputs.forEach((input) => {
            input.checked = toggle.checked;
          });
          updateCategoryState(categoryKey);
        });
      }

      categoryInputs.forEach((input) => {
        input.addEventListener("change", () => {
          updateCategoryState(categoryKey);
        });
      });

      updateCategoryState(categoryKey);
    });
  });
});
