"""Per-user Windows storage. Session cookies and API credentials use DPAPI."""
import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from pathlib import Path


def state_dir():
    return Path(os.environ.get("RAINDESK_DATA_DIR") or (Path(os.environ["LOCALAPPDATA"]) / "RainDesk"))


class _Blob(ctypes.Structure):
    _fields_ = [("length", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data, protect):
    buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
    source = _Blob(len(data), buf)
    result = _Blob()
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    fn = library.CryptProtectData if protect else library.CryptUnprotectData
    fn.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(result.data, result.length)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(result.data)


def save_private(name, value):
    target = state_dir() / name
    target.parent.mkdir(parents=True, exist_ok=True)
    data = _crypt(json.dumps(value, ensure_ascii=False).encode("utf-8"), True)
    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as temp:
        temp.write(data)
        filename = temp.name
    try:
        os.replace(filename, target)
    finally:
        Path(filename).unlink(missing_ok=True)


def load_private(name):
    path = state_dir() / name
    if not path.is_file():
        return None
    return json.loads(_crypt(path.read_bytes(), False).decode("utf-8"))
