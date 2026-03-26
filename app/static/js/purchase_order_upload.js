(function() {
    document.addEventListener('DOMContentLoaded', function() {
        const uploadForm = document.getElementById('upload-po-form');
        const dropzone = document.getElementById('upload-po-dropzone');
        const fileInput = document.getElementById('upload-po-file-input');
        const fileName = document.getElementById('upload-po-file-name');
        const browseButton = document.getElementById('upload-po-browse-btn');
        const errorAlert = document.getElementById('upload-po-error');
        const vendorSelect = document.getElementById('upload-po-vendor');

        if (!uploadForm || !dropzone || !fileInput) {
            return;
        }

        const clearError = () => {
            if (errorAlert) {
                errorAlert.classList.add('d-none');
                errorAlert.textContent = '';
            }
        };

        const setFile = (file) => {
            const dataTransfer = new DataTransfer();
            dataTransfer.items.add(file);
            fileInput.files = dataTransfer.files;
            if (fileName) {
                fileName.textContent = file.name;
                fileName.classList.remove('text-muted');
            }
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

        dropzone.addEventListener('click', () => fileInput.click());
        dropzone.addEventListener('keypress', (event) => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                fileInput.click();
            }
        });

        if (browseButton) {
            browseButton.addEventListener('click', (event) => {
                event.preventDefault();
                fileInput.click();
            });
        }

        fileInput.addEventListener('change', () => {
            handleFiles(fileInput.files);
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

            if (!fileInput.files.length) {
                event.preventDefault();
                if (errorAlert) {
                    errorAlert.textContent = 'Choose a file before uploading.';
                    errorAlert.classList.remove('d-none');
                }
                fileInput.focus();
            }
        });
    });
})();
