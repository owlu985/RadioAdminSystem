from io import BytesIO
from pathlib import Path

from flask import send_file
from mutagen import File, MutagenError
from mutagen.mp4 import MP4Cover


def artwork_for(path, root):
    if not path or not root:
        return None
    try:
        root = Path(root).resolve()
        track = Path(path).resolve()
        if not track.is_relative_to(root) or not track.is_file():
            return None
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            sidecar = track.with_suffix(ext).resolve()
            if sidecar.is_relative_to(root) and sidecar.is_file() and sidecar.stat().st_size:
                return send_file(sidecar, max_age=300)
        audio = File(track, easy=False)
        if audio is None:
            return None
        tags = audio.tags
        pictures = getattr(audio, "pictures", []) or (tags.getall("APIC") if hasattr(tags, "getall") else [])
        for picture in sorted(pictures, key=lambda p: p.type != 3):
            if picture.data and picture.mime.startswith("image/"):
                return send_file(BytesIO(picture.data), mimetype=picture.mime, max_age=300)
        covers = tags.get("covr") if tags else None
        if covers:
            mime = "image/png" if covers[0].imageformat == MP4Cover.FORMAT_PNG else "image/jpeg"
            return send_file(BytesIO(bytes(covers[0])), mimetype=mime, max_age=300)
    except (OSError, ValueError, MutagenError):
        return None
    return None
