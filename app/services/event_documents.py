from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from flask import current_app
from werkzeug.utils import secure_filename

from app import db
from app.models import EventDocument

EVENT_DOCUMENT_ALLOWED_EXTENSIONS = {
    "csv",
    "doc",
    "docx",
    "gif",
    "jpeg",
    "jpg",
    "pdf",
    "png",
    "ppt",
    "pptx",
    "txt",
    "webp",
    "xls",
    "xlsx",
}


class EventDocumentError(ValueError):
    """Raised when an event document upload cannot be accepted."""


def event_document_storage_dir() -> Path:
    upload_root = current_app.config["UPLOAD_FOLDER"]
    return Path(upload_root) / "event_documents"


def event_document_accept_attribute() -> str:
    return ",".join(f".{extension}" for extension in sorted(EVENT_DOCUMENT_ALLOWED_EXTENSIONS))


def _normalize_document_name(name: str | None) -> str | None:
    normalized = (name or "").strip()
    if not normalized:
        return None
    if "/" in normalized or "\\" in normalized:
        raise EventDocumentError("Document name cannot contain path separators.")
    if normalized in {".", ".."}:
        raise EventDocumentError("Choose a more descriptive document name.")
    return normalized


def persist_event_document_upload(
    storage,
    *,
    event_id: int,
    document_name: str | None,
    use_current_filename: bool,
    uploaded_by_id: int | None,
) -> EventDocument:
    raw_filename = (storage.filename or "").strip()
    original_filename = raw_filename.replace("\\", "/").split("/")[-1].strip()
    safe_filename = secure_filename(original_filename)
    if not safe_filename:
        raise EventDocumentError("Choose a valid document file to upload.")

    extension = Path(safe_filename).suffix.lower().lstrip(".")
    if extension not in EVENT_DOCUMENT_ALLOWED_EXTENSIONS:
        raise EventDocumentError(
            "Only PDF, Office, text, CSV, and image files are allowed."
        )

    content = storage.read()
    if not content:
        raise EventDocumentError("The uploaded document file was empty.")

    max_upload_size = int(current_app.config.get("MAX_UPLOAD_FILE_SIZE_BYTES", 0))
    if max_upload_size > 0 and len(content) > max_upload_size:
        raise EventDocumentError("The uploaded document file is too large.")

    content_type = getattr(storage, "mimetype", None) or mimetypes.guess_type(original_filename)[0]
    sha256 = hashlib.sha256(content).hexdigest()
    persisted_extension = Path(safe_filename).suffix.lower()

    storage_dir = event_document_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    persisted_path = storage_dir / f"{sha256}{persisted_extension}"
    if not persisted_path.exists():
        persisted_path.write_bytes(content)

    document = EventDocument(
        event_id=event_id,
        name=None if use_current_filename else _normalize_document_name(document_name),
        original_filename=original_filename,
        content_type=content_type,
        file_size_bytes=len(content),
        sha256=sha256,
        storage_path=str(persisted_path),
        uploaded_by=uploaded_by_id,
    )
    db.session.add(document)
    db.session.commit()
    return document


def resolve_event_document_path(document: EventDocument) -> str | None:
    storage_path = (document.storage_path or "").strip()
    if not storage_path:
        return None

    normalized_path = os.path.abspath(storage_path)
    if not os.path.isfile(normalized_path):
        return None
    return normalized_path


def cleanup_orphaned_event_document_storage(storage_path: str | None) -> None:
    if not storage_path:
        return
    normalized_path = os.path.abspath(storage_path)
    still_referenced = (
        EventDocument.query.filter_by(storage_path=normalized_path).count() > 0
    )
    if still_referenced:
        return
    if os.path.exists(normalized_path):
        os.remove(normalized_path)


def delete_event_document(document: EventDocument) -> None:
    storage_path = document.storage_path
    db.session.delete(document)
    db.session.commit()
    cleanup_orphaned_event_document_storage(storage_path)
