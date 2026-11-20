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
    """Own drafts and submission receipts for one student in one lesson."""
    def __init__(self, client, settings, *, remember=True):
        self.client = client
        self.settings = settings
        self.entries = {}
        self.active = True
        self.online = False
        self.remember = remember
        self.lock = threading.RLock()
        self.receipts = (load_private("receipts.dat") or {}) if remember else {}
        self._auto_epoch = 0

    def receipt_key(self, q):
        return json.dumps([self.client.uid, q.lesson_id, q.id, int(q.opened)], ensure_ascii=False)

    def set_auto_submit(self, enabled):
        with self.lock:
            self.settings["auto_submit"] = bool(enabled)
            self._auto_epoch += 1

    def auto_ticket(self, entry):
        """An opt-in that applies to this new release and this unedited draft only."""
        with self.lock:
            if (self.settings.get("auto_submit") is True and not entry.question.from_history
                    and self.active and self.online and entry.state == "解题中"):
                return self._auto_epoch, entry.revision
        return None

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
                value = remembered.get("draft", remembered.get("result"))
                if isinstance(value, list):
                    entry.text = ("\n" if q.kind == 4 else "、").join(str(x) for x in value)
                elif isinstance(value, str):
                    entry.text = value
                if "draft" in remembered and remembered.get("state") == "待确认":
                    entry.state = "待确认"
                remembered = remembered.get("state")
            if remembered in ("已提交", "提交待核对", "已自行作答"):
                entry.state = remembered
            self.entries[q.key] = entry
            return entry

    def record(self, entry, state, **details):
        with self.lock:
            key = self.receipt_key(entry.question)
            updated = self.receipts.copy()
            current = updated.pop(key, None)
            record = {**(current if isinstance(current, dict) else {}), **details, "state": state}
            if state != "已自行作答":
                record.pop("before_mark", None)
            if state not in ("已自行作答", "待确认", "待解题"):
                record.pop("draft", None)
            updated[key] = record
            if self.remember:
                updated = dict(list(updated.items())[-500:])
                save_private("receipts.dat", updated)
            self.receipts, entry.receipt = updated, record

    def completed_elsewhere(self, entry):
        with self.lock:
            if self.entries.get(entry.question.key) is not entry or entry.state in ("提交中", "已提交", "已自行作答"):
                return
            self.record(entry, "已自行作答", before_mark=entry.state, draft=entry.text)
            entry.revision += 1
            entry.state = "已自行作答"

    def undo_completed_elsewhere(self, entry):
        with self.lock:
            if self.entries.get(entry.question.key) is not entry or entry.state != "已自行作答":
                return
            marker = entry.receipt or {}
            previous = marker.get("before_mark")
            if marker.get("code") == 0:
                state = "已提交"
            elif previous == "提交待核对" or (previous is None and "mode" in marker and "result" in marker):
                # Older markers can retain a dispatched answer without its acknowledgement.
                state = "提交待核对"
            else:
                state = "待确认" if entry.text.strip() else "待解题"
            self.record(entry, state, draft=entry.text)
            entry.revision += 1
            entry.state = state

    def preview(self, entry):
        q = entry.question
        if (not self.active or not self.online or self.entries.get(q.key) is not entry
                or q.lesson_id != self.client.lesson.id or q.expired):
            raise ValueError("课堂已断开、题目已更换或作答已结束。")
        if entry.state in ("已提交", "提交中", "已自行作答", "提交待核对"):
            raise ValueError(entry.state)
        return q.for_submission(entry.text, self.settings.get("signature", ""))

    def confirm(self, entry, approved_result, *, auto_ticket=None, expected_revision=None):
        with self.lock:
            if expected_revision is not None and entry.revision != expected_revision:
                raise ValueError("作答状态或草稿已改变，请重新确认。")
            if auto_ticket is not None and (
                    self.settings.get("auto_submit") is not True or
                    auto_ticket != (self._auto_epoch, entry.revision) or entry.question.from_history or
                    entry.state != "待确认"):
                return False
            result = self.preview(entry)
            if result != approved_result:
                raise ValueError("答案或署名已经改变，请重新确认。")
            # Persist uncertainty before dispatch so exiting mid-request cannot trigger a replay.
            self.record(entry, "提交待核对", result=result, mode="自动" if auto_ticket is not None else "手动")
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
        return True
