"""Crash-safe text writes for the JSON state files the agent keeps on disk."""

import os
import tempfile
from pathlib import Path


def atomic_write_text(path, text, encoding='utf-8'):
    """Replace ``path`` only after the new content is fully on disk.

    An interrupted write leaves the previous file untouched, so a killed
    process cannot turn a session or approval store into truncated JSON.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    # The temp file must share the directory so os.replace stays atomic.
    handle, temp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f'.{path.name}.',
        suffix='.tmp',
    )

    try:
        with os.fdopen(handle, 'w', encoding=encoding, newline='\n') as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())

        os.replace(temp_name, path)

    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise

    return path
