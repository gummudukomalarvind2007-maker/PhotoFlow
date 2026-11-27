from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
import sqlite3
from pathlib import Path


def application_data_dir() -> Path:
    """Return PhotoFlow-owned state storage; never read or modify user media."""
    configured = os.environ.get("PHOTOFLOW_DATA_DIR")
    if configured:
        base = Path(configured)
    else:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = Path(local_app_data) / "PhotoFlow" if local_app_data else Path.home() / "AppData" / "Local" / "PhotoFlow"
    base.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def database_path() -> Path:
    return application_data_dir() / "photoflow.db"


def thumbnails_dir() -> Path:
    """Return PhotoFlow-owned thumbnail cache; source media is never a cache target."""
    path = application_data_dir() / "thumbnails"
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    return path


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(database_path(), timeout=30, isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA secure_delete = ON")
        connection.execute("BEGIN")
        try:
            os.chmod(database_path(), 0o600)
        except OSError:
            pass
        yield connection
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()

