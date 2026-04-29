(function() {
    document.addEventListener('DOMContentLoaded', function() {
        const uploadForm = document.getElementById('upload-po-form');
        const dropzone = document.getElementById('upload-po-dropzone');
        const fileInput = document.getElementById('upload-po-file-input');
        const fileName = document.getElementById('upload-po-file-name');
        const browseButton = document.getElementById('upload-po-browse-btn');
        const errorAlert = document.getElementById('upload-po-error');
        const vendorSelect = document.getElementById('upload-po-vendor');
        let pendingDroppedFile = null;

        if (!uploadForm || !dropzone || !fileInput) {
            return;
        }

        const clearError = () => {
            if (errorAlert) {
                errorAlert.classList.add('d-none');
                errorAlert.textContent = '';
            }
        };

        const setSelectedFileLabel = (file) => {
            if (!fileName) {
                return;
            }
            if (file && file.name) {
                fileName.textContent = file.name;
                fileName.classList.remove('text-muted');
                return;
            }
            fileName.textContent = 'No file chosen';
            fileName.classList.add('text-muted');
        };

        const openFilePicker = () => {
            clearError();
            if (typeof fileInput.showPicker === 'function') {
                fileInput.showPicker();
                return;
            }
            fileInput.click();
        };

        const setFile = (file) => {
            pendingDroppedFile = null;
            let assignedToInput = false;

            if (typeof DataTransfer !== 'undefined') {
                try {
                    const dataTransfer = new DataTransfer();
                    dataTransfer.items.add(file);
                    fileInput.files = dataTransfer.files;
                    assignedToInput = Boolean(fileInput.files && fileInput.files.length);
                } catch (error) {
                    assignedToInput = false;
                }
            }

            if (!assignedToInput) {
                pendingDroppedFile = file;
            }

            setSelectedFileLabel(file);
            clearError();
        };

        const handleFiles = (files) => {
            if (!files || !files.length) {
                return;
            }
            const [file] = files;
            if (file) {
                setFile(file);
            }
        };

        const activateDropzone = () => {
            dropzone.classList.add('border-primary', 'bg-primary', 'bg-opacity-10');
            dropzone.classList.remove('border-secondary');
        };

        const deactivateDropzone = () => {
            dropzone.classList.remove('border-primary', 'bg-primary', 'bg-opacity-10');
            dropzone.classList.add('border-secondary');
        };

        ['dragenter', 'dragover'].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                event.stopPropagation();
                activateDropzone();
            });
        });

        ['dragleave', 'dragend', 'drop'].forEach((eventName) => {
            dropzone.addEventListener(eventName, (event) => {
                event.preventDefault();
                event.stopPropagation();
                deactivateDropzone();
            });
        });

        dropzone.addEventListener('drop', (event) => {
            handleFiles(event.dataTransfer?.files);
        });

        dropzone.addEventListener('click', () => openFilePicker());
        dropzone.addEventListener('keypress', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                openFilePicker();
            }
        });

        if (browseButton) {
            browseButton.addEventListener('click', (event) => {
                event.preventDefault();
                openFilePicker();
            });
        }

        fileInput.addEventListener('change', () => {
            pendingDroppedFile = null;
            const [file] = Array.from(fileInput.files || []);
            setSelectedFileLabel(file || null);
            clearError();
        });

        uploadForm.addEventListener('submit', (event) => {
            if (vendorSelect && !vendorSelect.value) {
                event.preventDefault();
                if (errorAlert) {
                    errorAlert.textContent = 'Select a vendor before uploading.';
                    errorAlert.classList.remove('d-none');
                }
                vendorSelect.focus();
                return;
            }

            const hasNativeFile = Boolean(fileInput.files && fileInput.files.length);
            if (!hasNativeFile && !pendingDroppedFile) {
                event.preventDefault();
                if (errorAlert) {
                    errorAlert.textContent = 'Choose a file before uploading.';
                    errorAlert.classList.remove('d-none');
                }
                fileInput.focus();
                return;
            }

            if (!hasNativeFile && pendingDroppedFile) {
                event.preventDefault();
                const formData = new FormData(uploadForm);
                formData.set(
                    'purchase_order_file',
                    pendingDroppedFile,
                    pendingDroppedFile.name
                );

                fetch(uploadForm.action, {
                    method: uploadForm.method || 'POST',
                    body: formData,
                    credentials: 'same-origin',
                })
                    .then((response) => {
                        if (!response.ok) {
                            throw new Error('Upload failed');
                        }
                        window.location.assign(response.url);
                    })
                    .catch(() => {
                        if (errorAlert) {
                            errorAlert.textContent =
                                'The file could not be uploaded. Try choosing the file directly.';
                            errorAlert.classList.remove('d-none');
                        }
                    });
            }
        });
    });
})();
