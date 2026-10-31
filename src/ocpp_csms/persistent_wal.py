"""Keep SQLite WAL sidecars available for separate read-only consumers.

The stdlib sqlite3 connection does not expose sqlite3_file_control; this small
owner-side bridge uses SQLite's supported public C interface, not CPython
connection internals. The handle is kept open for the serving process lifetime.
"""
from contextlib import contextmanager
import ctypes
import ctypes.util
from pathlib import Path


@contextmanager
def persistent_wal(data_dir: str | Path):
    database = Path(data_dir) / "ocpp-csms.sqlite3"
    library_name = ctypes.util.find_library("sqlite3")
    if not library_name:
        raise RuntimeError("SQLite shared library not available for persistent WAL")
    library = ctypes.CDLL(library_name)
    library.sqlite3_open_v2.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_char_p]
    library.sqlite3_open_v2.restype = ctypes.c_int
    library.sqlite3_file_control.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
    library.sqlite3_file_control.restype = ctypes.c_int
    library.sqlite3_exec.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    library.sqlite3_exec.restype = ctypes.c_int
    library.sqlite3_close.argtypes = [ctypes.c_void_p]
    library.sqlite3_close.restype = ctypes.c_int
    handle = ctypes.c_void_p()
    # SQLITE_OPEN_READWRITE=2; never silently create a missing evidence database.
    rc = library.sqlite3_open_v2(str(database).encode(), ctypes.byref(handle), 2, None)
    if rc:
        if handle:
            library.sqlite3_close(handle)
        raise RuntimeError(f"Cannot open CSMS persistent WAL keeper: SQLite {rc}")
    try:
        # Opening a SQLite handle alone does not enter WAL mode or create the
        # shared-memory index. Activate the WAL pager while we still own the
        # connection; otherwise transient writer connections can remove both
        # sidecars even though the nominal keeper remains open.
        rc = library.sqlite3_exec(handle, b"SELECT name FROM sqlite_master LIMIT 1", None, None, None)
        if rc:
            raise RuntimeError(f"Cannot activate CSMS WAL keeper: SQLite {rc}")
        setting = ctypes.c_int(1)
        # SQLITE_FCNTL_PERSIST_WAL=10, a public SQLite file control.
        rc = library.sqlite3_file_control(handle, b"main", 10, ctypes.byref(setting))
        if rc:
            raise RuntimeError(f"Cannot enable persistent CSMS WAL: SQLite {rc}")
        if not database.with_name(database.name + "-shm").exists():
            raise RuntimeError("CSMS WAL keeper did not create a shared-memory sidecar")
        yield
    finally:
        rc = library.sqlite3_close(handle)
        if rc:
            raise RuntimeError(f"Cannot close CSMS WAL keeper: SQLite {rc}")
