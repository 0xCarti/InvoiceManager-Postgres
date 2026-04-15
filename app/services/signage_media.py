from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from app import db
from app.models import SignageMediaAsset

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "svg"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v"}


class SignageMediaError(ValueError):
    """Raised when a signage media upload cannot be accepted."""


def signage_media_storage_dir() -> Path:
    upload_root = current_app.config["UPLOAD_FOLDER"]
    return Path(upload_root) / "signage_media"


def detect_signage_media_type(
    filename: str | None,
    content_type: str | None = None,
) -> str | None:
    extension = Path(filename or "").suffix.lower().lstrip(".")
    if extension in IMAGE_EXTENSIONS:
        return SignageMediaAsset.TYPE_IMAGE
    if extension in VIDEO_EXTENSIONS:
        return SignageMediaAsset.TYPE_VIDEO

    normalized_content_type = (content_type or "").lower()
    if normalized_content_type.startswith("image/"):
        return SignageMediaAsset.TYPE_IMAGE
    if normalized_content_type.startswith("video/"):
        return SignageMediaAsset.TYPE_VIDEO
    return None


def allowed_signage_media_extensions() -> set[str]:
    return set(IMAGE_EXTENSIONS | VIDEO_EXTENSIONS)


def persist_signage_media_upload(
    storage,
    *,
    asset_name: str | None,
    uploaded_by_id: int | None,
) -> SignageMediaAsset:
    filename = secure_filename(storage.filename or "")
    if not filename:
        raise SignageMediaError("Choose a valid media file to upload.")

    media_type = detect_signage_media_type(filename, getattr(storage, "mimetype", None))
    if media_type is None:
        raise SignageMediaError("Only image and video files are supported.")

    content = storage.read()
    if not content:
        raise SignageMediaError("The uploaded media file was empty.")

    max_upload_size = int(current_app.config.get("MAX_UPLOAD_FILE_SIZE_BYTES", 0))
    if max_upload_size > 0 and len(content) > max_upload_size:
        raise SignageMediaError("The uploaded media file is too large.")

    content_type = getattr(storage, "mimetype", None) or mimetypes.guess_type(filename)[0]
    sha256 = hashlib.sha256(content).hexdigest()
    extension = Path(filename).suffix.lower()

    storage_dir = signage_media_storage_dir()
    storage_dir.mkdir(parents=True, exist_ok=True)
    persisted_path = storage_dir / f"{sha256}{extension}"
    if not persisted_path.exists():
        persisted_path.write_bytes(content)

    asset = SignageMediaAsset(
        name=(asset_name or "").strip() or None,
        original_filename=filename,
        media_type=media_type,
        content_type=content_type,
        file_size_bytes=len(content),
        sha256=sha256,
        storage_path=str(persisted_path),
        uploaded_by=uploaded_by_id,
    )
    db.session.add(asset)
    db.session.commit()
    return asset


def delete_signage_media_asset(asset: SignageMediaAsset) -> None:
    storage_path = asset.storage_path
    db.session.delete(asset)
    db.session.commit()

    still_referenced = (
        SignageMediaAsset.query.filter_by(storage_path=storage_path).count() > 0
    )
    if still_referenced:
        return

    if storage_path and os.path.exists(storage_path):
        os.remove(storage_path)


def signage_media_public_url(asset: SignageMediaAsset) -> str:
    return url_for(
        "signage.signage_media_file",
        asset_id=asset.id,
        filename=asset.original_filename,
    )
