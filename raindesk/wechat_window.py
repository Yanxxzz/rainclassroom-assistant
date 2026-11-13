"""Identify the selected Windows chat and invoke its own QR context-menu item."""
import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
from pathlib import PureWindowsPath
import time


def binding(dll, name, result, *args):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, result
    return fn


_user = ctypes.WinDLL("user32", use_last_error=True)
_kernel = ctypes.WinDLL("kernel32", use_last_error=True)
_callback = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
_enum = binding(_user, "EnumWindows", w.BOOL, _callback, w.LPARAM)
_pid = binding(_user, "GetWindowThreadProcessId", w.DWORD, w.HWND, ctypes.POINTER(w.DWORD))
_title = binding(_user, "GetWindowTextW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
_class = binding(_user, "GetClassNameW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
_visible = binding(_user, "IsWindowVisible", w.BOOL, w.HWND)
minimized = binding(_user, "IsIconic", w.BOOL, w.HWND)
_open = binding(_kernel, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
_image = binding(_kernel, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE, w.DWORD,
                 w.LPWSTR, ctypes.POINTER(w.DWORD))
_close = binding(_kernel, "CloseHandle", w.BOOL, w.HANDLE)
_bounds = binding(ctypes.WinDLL("dwmapi"), "DwmGetWindowAttribute", w.LONG, w.HWND,
                  w.DWORD, ctypes.c_void_p, w.DWORD)
_window_rect = binding(_user, "GetWindowRect", w.BOOL, w.HWND, ctypes.POINTER(w.RECT))
_to_client = binding(_user, "ScreenToClient", w.BOOL, w.HWND, ctypes.POINTER(w.POINT))
_child = binding(_user, "ChildWindowFromPointEx", w.HWND, w.HWND, w.POINT, w.UINT)
_post = binding(_user, "PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


def window_pid(hwnd):
    value = w.DWORD()
    _pid(hwnd, ctypes.byref(value))
    return value.value


def caption(hwnd, func=_title):
    buf = ctypes.create_unicode_buffer(512)
    func(hwnd, buf, len(buf))
    return buf.value


def handles():
    windows = []

    @_callback
    def visit(hwnd, _):
        if _visible(hwnd):
            windows.append(hwnd)
        return True

    _enum(visit, 0)
    return windows


def process_name(pid):
    handle = _open(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        size = w.DWORD(1024)
        buf = ctypes.create_unicode_buffer(size.value)
        if _image(handle, 0, buf, ctypes.byref(size)):
            return PureWindowsPath(buf.value).name.casefold()
        return ""
    finally:
        _close(handle)


@dataclass(frozen=True)
class ChatWindow:
    hwnd: int
    pid: int
    title: str
    window_class: str

    def matches(self):
        return (window_pid(self.hwnd) == self.pid and caption(self.hwnd) == self.title
                and caption(self.hwnd, _class) == self.window_class)

    def available(self):
        return self.matches() and _visible(self.hwnd) and not minimized(self.hwnd)


def chat_windows():
    processes = {}
    result = []
    for hwnd in handles():
        pid = window_pid(hwnd)
        if pid not in processes:
            processes[pid] = process_name(pid)
        if processes[pid] not in {"weixin.exe", "wechat.exe"}:
            continue
        title = caption(hwnd)
        # The main contact-list window can switch conversations without changing HWND.
        if not title or title.casefold() in {"微信", "wechat", "weixin", "图片", "图片查看器"}:
            continue
        if title.strip("()0123456789+ ").casefold() not in {"微信", "wechat", "weixin"}:
            result.append(ChatWindow(hwnd, pid, title, caption(hwnd, _class)))
    return sorted(result, key=lambda item: (item.title, item.hwnd))


def _right_click(target, hit):
    rect = w.RECT()
    for attribute in (9, 0):
        ok = (_bounds(target.hwnd, attribute, ctypes.byref(rect), ctypes.sizeof(rect)) == 0
              if attribute else _window_rect(target.hwnd, ctypes.byref(rect)))
        if ok and abs(rect.right - rect.left - hit.size[0]) <= 3 and abs(rect.bottom - rect.top - hit.size[1]) <= 3:
            break
    else:
        return False  # A resize or DPI transition needs a fresh capture.
    screen = (rect.left + hit.center[0], rect.top + hit.center[1])
    hwnd = target.hwnd
    for _ in range(12):
        point = w.POINT(*screen)
        if not _to_client(hwnd, ctypes.byref(point)):
            return False
        child = _child(hwnd, point, 7)  # skip invisible, disabled and transparent children
        if not child or child == hwnd or window_pid(child) != target.pid:
            break
        hwnd = child
    else:
        return False
    if not target.available():
        return False
    packed = (screen[0] & 0xffff) | ((screen[1] & 0xffff) << 16)
    return bool(_post(hwnd, 0x007B, hwnd, packed))  # WM_CONTEXTMENU uses screen coordinates.


_SCAN_LABELS = {"识别图中二维码", "识别图片中的二维码", "识别二维码", "識別圖中二維碼",
                "掃描圖片中的行動條碼", "scan qr code in image", "scan qr code",
                "extract qr code", "recognize qr code in image"}


def scan_in_wechat(target, hit, still_current):
    """Use only a newly opened menu owned by the selected WeChat process."""
    import uiautomation as auto

    with auto.UIAutomationInitializerInThread():
        before = set(handles())
        if not still_current() or not target.available() or not _right_click(target, hit):
            return False
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and still_current() and target.available():
            for hwnd in set(handles()).difference(before):
                if window_pid(hwnd) != target.pid:
                    continue
                root = auto.ControlFromHandle(hwnd)
                if not root:
                    continue
                for index, (item, _) in enumerate(auto.WalkControl(root, includeTop=True, maxDepth=5)):
                    if index > 80:
                        break
                    if item.ControlType != auto.ControlType.MenuItemControl:
                        continue
                    if item.Name.replace("&", "").strip().casefold() not in _SCAN_LABELS:
                        continue
                    action = item.GetPattern(auto.PatternId.InvokePattern)
                    if not still_current() or not target.available():
                        return False
                    if action and action.Invoke():
                        return True
                    legacy = item.GetPattern(auto.PatternId.LegacyIAccessiblePattern)
                    if legacy and still_current() and target.available():
                        return bool(legacy.DoDefaultAction())
            time.sleep(.1)
    return False
