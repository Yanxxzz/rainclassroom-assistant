from dataclasses import dataclass
import json
import threading

import requests

from .client import RainError, UnclearReceipt
from .local import load_private, save_private
from .question import Question


@dataclass
class Entry:
    question: Question
    text: str = ""
    explanation: str = ""
    state: str = "待解题"
    revision: int = 0
    picture: bytes | None = None
    receipt: dict | None = None


class Desk:
    """One account and one lesson per desk. Only confirm() can send a response."""
    def __init__(self, client, settings, *, remember=True):
        self.client = client
        self.settings = settings
        self.entries = {}
        self.active = True
        self.online = False
        self.remember = remember
        self.lock = threading.RLock()
        self.receipts = (load_private("receipts.dat") or {}) if remember else {}

    def receipt_key(self, q):
        return json.dumps([self.client.uid, q.lesson_id, q.id, int(q.opened)], ensure_ascii=False)

    def add(self, q):
        with self.lock:
            if not self.active or q.lesson_id != self.client.lesson.id or q.expired:
                return None
            old = self.entries.get(q.key)
            if old and not (old.question.expired and q.opened > old.question.opened + 1):
                return None
            entry = Entry(q)
            remembered = self.receipts.get(self.receipt_key(q))
            if isinstance(remembered, dict):
                entry.receipt = remembered
                value = remembered.get("result")
                if isinstance(value, list):
                    entry.text = ("\n" if q.kind == 4 else "、").join(str(x) for x in value)
                elif isinstance(value, str):
                    entry.text = value
                remembered = remembered.get("state")
            if remembered in ("已提交", "提交待核对", "已自行作答"):
                entry.state = remembered
            self.entries[q.key] = entry
            return entry

    def record(self, entry, state, **details):
        key = self.receipt_key(entry.question)
        current = self.receipts.get(key)
        record = {**(current if isinstance(current, dict) else {}), **details, "state": state}
        self.receipts[key] = entry.receipt = record
        if self.remember:
            save_private("receipts.dat", dict(list(self.receipts.items())[-500:]))

    def completed_elsewhere(self, entry):
        with self.lock:
            if entry.state == "提交中":
                return
            entry.revision += 1
            entry.state = "已自行作答"
            self.record(entry, entry.state)

    def preview(self, entry):
        q = entry.question
        if (not self.active or not self.online or self.entries.get(q.key) is not entry
                or q.lesson_id != self.client.lesson.id or q.expired):
            raise ValueError("课堂已断开、题目已更换或作答已结束。")
        if entry.state in ("已提交", "提交中", "已自行作答", "提交待核对"):
            raise ValueError(entry.state)
        return q.for_submission(entry.text, self.settings.get("signature", ""))

    def confirm(self, entry, approved_result):
        with self.lock:
            result = self.preview(entry)
            if result != approved_result:
                raise ValueError("答案或署名已经改变，请重新确认。")
            # Persist uncertainty before dispatch so exiting mid-request cannot trigger a replay.
            self.record(entry, "提交待核对", result=result)
            entry.state = "提交中"
        try:
            receipt = self.client.submit(entry.question, result)
        except RainError:
            with self.lock:
                entry.state = "待确认"
                self.record(entry, "待确认")
            raise
        except (UnclearReceipt, OSError, requests.RequestException):
            with self.lock:
                entry.state = "提交待核对"
            raise ValueError("未收到提交回执，请先在微信核对这道题，再决定是否重试。")
        with self.lock:
            entry.state = "已提交"
            self.record(entry, "已提交", **receipt)
