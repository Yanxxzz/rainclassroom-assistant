import time
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from raindesk.client import Lesson, RainClient
from raindesk.live import LessonEvents
from raindesk.ui import App


class AutoSubmitWorkflow(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError:
            self.skipTest("Tk display required for review-window interaction")
        self.root.withdraw()
        self.client = RainClient()
        self.client.uid, self.client.bearer = "student", "example"
        self.client.lesson = Lesson("demo", "演练")
        self.client.presentation = Mock(side_effect=self.presentation)
        self.client.submit = Mock(return_value={"time": 1, "http": 200, "code": 0, "message": ""})
        self.store = patch("raindesk.ui.save_private").start()
        patch("raindesk.ui.load_private", return_value={"signature": "示例署名"}).start()
        patch("raindesk.ui.solve", return_value=("24 小时；60 分钟", "")).start()
        self.addCleanup(patch.stopall)
        self.app = App(self.root, initial_client=self.client, demo=True)
        self.addCleanup(self.app.close)
        self.jobs = []
        self.app.task = lambda fn, success, failure=None: self.jobs.append((fn, success, failure))
        self.app.monitor()
        self.stream = LessonEvents(self.client, self.app.receive)
        self.stream.handle({"op": "hello", "presentation": "deck", "timeline": []})

    @staticmethod
    def presentation(_pres):
        return {"slides": [{"problem": {"problemId": str(i), "problemType": 5, "body": "按顺序回答。"}}
                           for i in range(10)]}

    def release(self, n, history=False):
        data = {"prob": str(n), "dt": time.time() * 1000, "limit": 120}
        if history:
            self.stream.handle({"op": "hello", "presentation": "deck",
                                "timeline": [{"type": "problem", "pres": "deck", **data}]})
        else:
            self.stream.handle({"op": "unlockproblem", "problem": data})

    def run_job(self):
        fn, done, error = self.jobs.pop(0)
        try:
            result = fn()
        except (ValueError, OSError) as exc:
            if error is None:
                raise
            error(str(exc))
        else:
            done(result)

    def test_button_changes_future_releases_and_preserves_signature(self):
        self.release(0)
        self.run_job()
        self.assertFalse(self.app.auto_submit.get())
        self.assertEqual(self.app.entries[0].state, "待确认")
        self.app.auto_btn.invoke()
        self.assertTrue(self.app.config["auto_submit"])
        self.assertEqual(self.jobs, [])
        self.release(1, history=True)
        self.run_job()
        self.assertEqual(self.jobs, [])
        self.release(2)
        self.run_job()
        self.run_job()
        entry = self.app.entries[-1]
        self.client.submit.assert_called_once_with(entry.question, "示例署名\n24 小时；60 分钟")
        self.assertEqual((entry.state, entry.receipt["mode"]), ("已提交", "自动"))
        self.assertIsNone(self.app.active_dialog)
        self.app.auto_btn.invoke()
        self.assertFalse(self.app.config["auto_submit"])
        self.store.assert_called()
        self.release(3)
        self.run_job()
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.client.submit.call_count, 1)

    def test_queued_send_can_be_revoked_and_never_replayed(self):
        self.app.auto_btn.invoke()
        self.release(1)
        self.run_job()
        self.app.auto_btn.invoke()
        self.app.auto_btn.invoke()
        self.run_job()
        self.assertEqual(self.app.entries[-1].state, "待确认")
        self.client.submit.assert_not_called()
        self.release(2)
        self.run_job()
        self.stream.handle({"op": "lockproblem", "problem": {"prob": "2"}})
        self.run_job()
        self.client.submit.assert_not_called()
        self.release(3)
        self.client.submit.side_effect = TimeoutError()
        self.run_job()
        self.run_job()
        self.assertEqual(self.app.entries[-1].state, "提交待核对")
        self.release(3, history=True)
        self.assertEqual(self.jobs, [])
        self.assertEqual(self.client.submit.call_count, 1)

    def test_manual_edit_and_dismissed_question_cancel_inflight_draft(self):
        self.app.auto_btn.invoke()
        self.release(1)
        self.app.editor.insert("end", "我自己的答案")
        self.app.edit()
        self.run_job()
        self.assertEqual(self.app.current.text, "我自己的答案")
        self.release(2)
        e = self.app.entries[-1]
        self.app.desk.completed_elsewhere(e)
        self.run_job()
        self.assertEqual(e.state, "已自行作答")
        self.assertEqual(self.jobs, [])
        self.client.submit.assert_not_called()
