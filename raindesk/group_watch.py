"""On-device QR detection for one explicitly selected chat window."""
from dataclasses import dataclass
from hashlib import sha256
import queue
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

from .wechat_window import scan_in_wechat


@dataclass(frozen=True)
class Code:
    key: bytes
    kind: str
    lesson_id: str = ""


@dataclass(frozen=True)
class Hit:
    code: Code
    center: tuple
    size: tuple


def classify(text):
    if not isinstance(text, str) or len(text) > 2048 or any(c.isspace() for c in text):
        return None
    try:
        url = urlsplit(text)
        if url.scheme not in {"https", "http"} or url.username is not None or url.password is not None or url.port:
            return None
    except ValueError:
        return None
    key = sha256(text.encode()).digest()
    if url.hostname == "weixin.qq.com" and re.fullmatch(r"/q/[A-Za-z0-9_-]{6,256}/?", url.path):
        return Code(key, "wechat")
    if url.hostname in {"www.yuketang.cn", "yuketang.cn"}:
        values = parse_qs(url.query)
        lesson = values.get("lessonId") or values.get("lesson_id") or []
        if len(lesson) == 1 and re.fullmatch(r"[0-9]{1,24}", lesson[0]):
            return Code(key, "rain", lesson[0])
    return None


def read_codes(image):
    import zxingcpp

    width, height = image.size
    hits = []
    for barcode in zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.QRCode):
        code = classify(barcode.text)
        if code:
            p = barcode.position
            corners = (p.top_left, p.top_right, p.bottom_left, p.bottom_right)
            center = (sum(v.x for v in corners) // 4, sum(v.y for v in corners) // 4)
            hits.append(Hit(code, center, (width, height)))
    return sorted(hits, key=lambda hit: hit.center[1])


class GroupWatch:
    def __init__(self, target, emit):
        self.target = target
        self.emit = emit
        self.stop = threading.Event()
        self.requests = queue.Queue()
        self.lock = threading.Lock()
        self.visible = {}
        self.seen = set()
        self.selected_key = None
        self.frame_number = 0
        self.ready = threading.Event()

    def ingest(self, image):
        hits = read_codes(image)
        with self.lock:
            initial = self.frame_number == 0
            self.frame_number += 1
            self.visible = {h.code.key: h for h in hits}
            new = [h.code for h in self.visible.values() if h.code.key not in self.seen]
            self.seen.update(self.visible)
        self.ready.set()
        if new and not self.stop.is_set():
            self.emit("codes", (initial, new))

    def request_scan(self, code):
        with self.lock:
            self.selected_key = code.key
        self.requests.put(code.key)

    def cancel_scan(self):
        with self.lock:
            self.selected_key = None

    def run(self):
        control = None
        cap = None
        latest = None
        try:
            from PIL import Image
            from windows_capture import WindowsCapture

            if not self.target.available():
                raise ValueError("请还原所选群窗口，再开始监听。")
            cap = WindowsCapture(window_hwnd=self.target.hwnd, cursor_capture=False)

            @cap.event
            def on_frame_arrived(frame, frame_control):
                nonlocal latest
                if self.stop.is_set() or not self.target.matches():
                    frame_control.stop()
                    return
                if not self.target.available():
                    return
                try:
                    # The native buffer expires after this callback. Retain only
                    # the latest window frame so a final update cannot be skipped.
                    gray = Image.fromarray(frame.frame_buffer[:, :, 1].copy())
                    with self.lock:
                        latest = gray
                except Exception:
                    self.emit("error", "群窗口图像读取失败，请重新选择窗口。")
                    self.stop.set()
                    frame_control.stop()

            @cap.event
            def on_closed():
                self.stop.set()

            control = cap.start_free_threaded()
            started = time.monotonic()
            previous = None
            while not self.stop.wait(.25):
                if not self.target.matches() or control.is_finished():
                    break
                with self.lock:
                    image, latest = latest, None
                if not self.target.available():
                    status = "窗口已最小化或隐藏，监听已暂停。还原原窗口后会自动恢复。"
                    with self.lock:
                        self.visible.clear()
                else:
                    if image is not None:
                        low, high = image.getextrema()
                        if high - low > 12:
                            self.ingest(image)
                        else:
                            with self.lock:
                                self.visible.clear()
                    if self.ready.is_set():
                        status = "正在监听所选群窗口"
                    elif time.monotonic() - started > 10:
                        status = "尚未收到可读画面，请还原群窗口并让聊天内容显示。"
                    else:
                        status = "正在连接群窗口…"
                if status != previous:
                    self.emit("status", status)
                    previous = status
                try:
                    key = self.requests.get_nowait()
                except queue.Empty:
                    continue
                with self.lock:
                    hit = self.visible.get(key)

                def current():
                    with self.lock:
                        return (not self.stop.is_set() and self.selected_key == key
                                and self.visible.get(key) == hit)

                try:
                    invoked = bool(hit and self.target.available()
                                   and scan_in_wechat(self.target, hit, current))
                except Exception:
                    invoked = None  # An accessibility failure can occur after invocation.
                self.emit("scan", (key, invoked))
        except Exception:
            self.emit("error", "无法采集所选群窗口。请使用 Windows 10 1903 或更新版本，并还原微信窗口。")
        finally:
            self.stop.set()
            try:
                if control and not control.is_finished():
                    control.stop()
            finally:
                if cap is not None:
                    cap.frame_handler = cap.closed_handler = None
                latest = None
                self.emit("ended", None)
