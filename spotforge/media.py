"""Constrain media demuxing to actual containers, never playlist autodetection."""
from pathlib import Path


def local_input_options(path):
    formats = {".mp4":"mov", ".mov":"mov", ".m4a":"mov", ".webm":"matroska",
               ".mp3":"mp3", ".wav":"wav"}
    format = formats.get(Path(path).suffix.lower())
    if not format:
        raise ValueError("Nicht unterstütztes lokales Medienformat.")
    options = ["-protocol_whitelist", "file,pipe", "-f", format]
    if format == "mov":
        options += ["-enable_drefs", "0", "-use_absolute_path", "0"]
    return options
