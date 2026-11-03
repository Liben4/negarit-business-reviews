"""Local SQLite safety copies (production uses pg_dump; see README)."""
import os
import sqlite3
import time

KEEP = 10


def backup_sqlite(path: str) -> str | None:
    """Consistent copy of a SQLite file into <instance>/backups/. Returns the new file, or None if there is nothing to copy."""
    if not os.path.exists(path):
        return None
    folder = os.path.join(os.path.dirname(path), "backups")
    os.makedirs(folder, exist_ok=True)
    stem = os.path.splitext(os.path.basename(path))[0]
    dest = os.path.join(folder, f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.sqlite")
    src, dst = sqlite3.connect(path), sqlite3.connect(dest)
    try:
        src.backup(dst)  # safe even if the file is in use
    finally:
        dst.close()
        src.close()
    old = sorted(f for f in os.listdir(folder) if f.startswith(stem + "-"))[:-KEEP]
    for f in old:
        os.remove(os.path.join(folder, f))
    return dest
