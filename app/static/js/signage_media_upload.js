(function () {
    var uploadForm = document.getElementById("signage-media-upload-form");
    var dropzone = document.getElementById("signage-media-dropzone");
    var fileInput = document.getElementById("signage-media-file-input");
    var fileName = document.getElementById("signage-media-file-name");
    var browseButton = document.getElementById("signage-media-browse-btn");
    var errorAlert = document.getElementById("signage-media-upload-error");

    if (!uploadForm || !dropzone || !fileInput) {
        return;
    }

    function clearError() {
        if (!errorAlert) {
            return;
        }
        errorAlert.classList.add("d-none");
        errorAlert.textContent = "";
    }

    function setFile(file) {
        var dataTransfer;
        if (!file) {
            return;
        }
        dataTransfer = new DataTransfer();
        dataTransfer.items.add(file);
        fileInput.files = dataTransfer.files;
        if (fileName) {
            fileName.textContent = file.name;
            fileName.classList.remove("text-muted");
        }
        clearError();
    }

    function handleFiles(files) {
        if (!files || !files.length) {
            return;
        }
        setFile(files[0]);
    }

    function activateDropzone() {
        dropzone.classList.add("border-primary", "bg-primary", "bg-opacity-10");
        dropzone.classList.remove("border-secondary");
    }

    function deactivateDropzone() {
        dropzone.classList.remove("border-primary", "bg-primary", "bg-opacity-10");
        dropzone.classList.add("border-secondary");
    }

    ["dragenter", "dragover"].forEach(function (eventName) {
        dropzone.addEventListener(eventName, function (event) {
            event.preventDefault();
            event.stopPropagation();
            activateDropzone();
        });
    });

    ["dragleave", "dragend", "drop"].forEach(function (eventName) {
        dropzone.addEventListener(eventName, function (event) {
            event.preventDefault();
            event.stopPropagation();
            deactivateDropzone();
        });
    });

    dropzone.addEventListener("drop", function (event) {
        handleFiles(event.dataTransfer && event.dataTransfer.files);
    });

    dropzone.addEventListener("click", function () {
        fileInput.click();
    });

    dropzone.addEventListener("keypress", function (event) {
        if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            fileInput.click();
        }
    });

    if (browseButton) {
        browseButton.addEventListener("click", function (event) {
            event.preventDefault();
            fileInput.click();
        });
    }

    fileInput.addEventListener("change", function () {
        handleFiles(fileInput.files);
    });

    uploadForm.addEventListener("submit", function (event) {
        if (!fileInput.files.length) {
            event.preventDefault();
            if (errorAlert) {
                errorAlert.textContent = "Choose a file before uploading.";
                errorAlert.classList.remove("d-none");
            }
            fileInput.focus();
        }
    });
}());
