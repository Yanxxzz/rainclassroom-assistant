from dataclasses import replace
import json
import time

import requests
import websocket

from .client import RainError
from .question import Question, event_time, seconds


class LessonEvents:
    """Match explicit teacher releases to the corresponding slide by problem ID."""
    def __init__(self, client, emit):
        self.client, self.emit = client, emit
        self.current = ""
        self.slides = {}
        self.questions = {}
        self.pending = {}
        self.last_attempt = {}
        self.ended = False

    def release(self, data, current, history=False):
        pid = str(data.get("prob") or data.get("problemId") or data.get("sid") or "")
        if not pid:
            return
        pres = str(data.get("pres") or data.get("presentation") or current or "")
        opened, limit = event_time(data.get("dt")), seconds(data.get("limit"))
        if history and limit > 0 and opened and opened + limit <= time.time():
            return
        old = self.questions.get(pid)
        if old and (not opened or opened <= old.opened + 1 or not old.expired):
            return
        if history and pid in self.pending and not self.pending[pid][2]:
            return  # An initial timeline can arrive while a real release is waiting for its slide.
        self.pending[pid] = (pres, dict(data), history)

    def resolve(self):
        for pid, (pres, data, history) in list(self.pending.items()):
            pres = pres or self.current
            if not pres:
                continue
            deck = self.slides.get(pres, {})
            if pid not in deck and time.monotonic() - self.last_attempt.get((pres, pid), -100) >= 3:
                self.last_attempt[pres, pid] = time.monotonic()
                try:
                    response = self.client.presentation(pres)
                    deck = {str(s["problem"]["problemId"]): s for s in response["slides"]
                            if isinstance(s, dict) and isinstance(s.get("problem"), dict)
                            and s["problem"].get("problemId")}
                    self.slides[pres] = deck
                except (RainError, requests.RequestException):
                    self.emit("status", "已收到发题通知，正在重试读取题面。")
            if pid not in deck:
                continue
            q = Question.from_slide(self.client.lesson.id, pres, deck[pid], data, from_history=history)
            del self.pending[pid]
            if q.expired:
                continue
            self.questions[pid] = q
            self.emit("question", q)

    def handle(self, msg):
        op = msg.get("op")
        if op == "lessonfinished" or (op == "hello" and msg.get("message") == "lesson finished"):
            self.ended = True
            self.emit("ended", "本节课已结束。")
            return
        if op in ("hello", "showpresentation"):
            self.current = str(msg.get("presentation") or self.current)
            if op == "hello":
                self.emit("connected", None)
            for e in msg.get("timeline") or []:
                if isinstance(e, dict) and e.get("type") == "problem":
                    self.release(e, self.current, history=True)
        elif op == "unlockproblem":
            self.release(msg.get("problem") or {}, msg.get("presentation") or self.current)
        elif op == "presentationupdated":
            self.slides.pop(str(msg.get("presentation") or self.current), None)
        elif op in ("extendtime", "lockproblem"):
            data = msg.get("problem") or {}
            pid = str(data.get("prob") or data.get("problemId") or "")
            if not pid:
                active = list(dict.fromkeys([key for key, q in self.questions.items() if not q.expired]
                                            + list(self.pending)))
                if len(active) == 1:
                    pid = active[0]
            if pid in self.pending:
                if op == "lockproblem":
                    del self.pending[pid]
                else:
                    pending = self.pending[pid][1]
                    if seconds(pending.get("limit")) > 0:
                        pending["limit"] = seconds(pending["limit"]) + max(0, seconds(data.get("extend")))
            if pid in self.questions:
                q = self.questions[pid]
                if op == "lockproblem":
                    q = q.close()
                elif q.deadline and seconds(data.get("extend")) > 0:
                    q = replace(q, deadline=q.deadline + seconds(data["extend"]))
                self.questions[pid] = q
                self.emit("changed", q)
        self.resolve()


def listen(client, stop, emit):
    stream = LessonEvents(client, emit)
    delay = 1
    while not stop.is_set() and not stream.ended:
        ws = None
        try:
            ws = websocket.create_connection("wss://www.yuketang.cn/wsapp/", timeout=10,
                                             origin="https://www.yuketang.cn")
            ws.send(json.dumps({"op": "hello", "userid": client.uid, "role": "student",
                                "auth": client.lesson_token, "lessonid": client.lesson.id}))
            ws.settimeout(1.5)
            last_ping = time.monotonic()
            while not stop.is_set() and not stream.ended:
                if time.monotonic() - last_ping >= 20:
                    ws.ping()
                    last_ping = time.monotonic()
                try:
                    packet = ws.recv()
                except websocket.WebSocketTimeoutException:
                    stream.resolve()
                    continue
                if not packet:
                    raise ConnectionError("课堂连接已断开")
                msg = json.loads(packet)
                if isinstance(msg, dict):
                    if msg.get("op") == "hello":
                        delay = 1
                    emit("event_kind", (str(msg.get("op", "")), tuple(msg.keys())))
                    stream.handle(msg)
        except (websocket.WebSocketException, OSError, ValueError) as exc:
            if not stop.is_set():
                emit("offline", "课堂连接中断，%d 秒后重连（%s）。" % (delay, type(exc).__name__))
                stop.wait(delay)
                delay = min(20, delay * 2)
        finally:
            if ws:
                ws.close(timeout=1)
