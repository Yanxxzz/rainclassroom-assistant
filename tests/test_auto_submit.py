import time
import tkinter as tk
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
import unittest
from unittest.mock import Mock, patch

from raindesk.client import Lesson, RainClient
from raindesk.desk import submit_delay
from raindesk.live import LessonEvents
from raindesk.providers import SolverError
from raindesk.ui import App


def widgets(parent, cls):
    for child in parent.winfo_children():
        if isinstance(child, cls):
            yield child
        yield from widgets(child, cls)


def button(parent, label):
    return next(b for b in widgets(parent, ttk.Button) if b.cget("text") == label)


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
        patch("raindesk.ui.load_private", return_value={"signature": "示例署名", "submit_delay_seconds": 0}).start()
        patch("raindesk.solver.ask_gpt", return_value={"items": ["24 小时", "60 分钟"], "explanation": ""}).start()
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
        except (SolverError, ValueError, OSError) as exc:
            if error is None:
                raise
            error(str(exc))
        else:
            done(result)

    def select_delay(self, label):
        self.app.delay_box.set(label)
        self.app.delay_box.event_generate("<<ComboboxSelected>>")

    def test_presets_custom_seconds_and_cancel(self):
        self.assertEqual(submit_delay({}), 10)
        self.assertEqual(submit_delay({"submit_delay_seconds": -1}), 10)
        self.app.config.pop("submit_delay_seconds")
        self.app.show_delay()
        self.assertEqual(self.app.delay_box.get(), "10秒")
        for label, seconds in [("30秒", 30), ("1分钟", 60), ("立即", 0), ("10秒", 10)]:
            self.select_delay(label)
            self.assertEqual(self.app.config["submit_delay_seconds"], seconds)
        with patch("raindesk.ui.simpledialog.askinteger", side_effect=[75, None]):
            self.select_delay("自定义…")
            self.select_delay("自定义…")
        self.assertEqual((self.app.delay_box.get(), self.app.config["submit_delay_seconds"]), ("75秒", 75))
        self.store.assert_called_with("settings.dat", self.app.config)

    def test_independent_countdowns_start_when_each_draft_is_ready(self):
        self.select_delay("10秒")
        self.app.auto_btn.invoke()
        with patch("raindesk.desk.time.monotonic", return_value=100) as clock:
            self.release(0)
            clock.return_value = 120
            self.run_job()
            self.assertEqual(self.app.review_state.get(), "10秒后自动提交")
            self.release(1)
            clock.return_value = 125
            self.run_job()
            clock.return_value = 129.999
            self.app.process_submissions()
            self.assertFalse(self.jobs)
            clock.return_value = 130
            self.app.process_submissions()
            self.app.process_submissions()
            self.assertEqual(len(self.jobs), 1)
            self.run_job()
            self.assertEqual(self.client.submit.call_args.args[0].id, "0")
            self.assertIn("5秒", self.app.list.item("1", "text"))
            clock.return_value = 135
            self.app.process_submissions()
            self.run_job()
            self.assertEqual(self.client.submit.call_count, 2)

    def test_retime_queued_work_and_shorter_delay(self):
        self.select_delay("10秒")
        self.app.auto_btn.invoke()
        with patch("raindesk.desk.time.monotonic", return_value=10) as clock:
            self.release(2)
            self.run_job()
            clock.return_value = 20
            self.app.process_submissions()
            self.select_delay("1分钟")
            self.run_job()  # Even a queued worker observes the longer delay.
            self.client.submit.assert_not_called()
            self.assertIn("50秒", self.app.review_state.get())
            clock.return_value = 47
            self.select_delay("30秒")  # 37 seconds already elapsed.
            self.run_job()
            self.assertEqual(self.app.current.receipt["mode"], "自动")
            self.assertFalse(self.jobs)

    def test_manual_review_cancels_timer_and_deadline_never_sends_early(self):
        self.app.auto_btn.invoke()
        with patch("raindesk.ui.simpledialog.askinteger", return_value=250):
            self.select_delay("自定义…")
        self.release(0)
        self.run_job()
        self.assertIn("可提前手动提交", self.app.review_state.get())
        self.app.send_btn.invoke()
        self.assertIsNone(self.app.current.pending)
        button(self.app.active_dialog, "确认提交").invoke()
        self.run_job()
        self.assertEqual(self.app.current.receipt["mode"], "手动")
        self.release(1)
        self.run_job()
        self.stream.handle({"op": "lockproblem", "problem": {"prob": "1"}})
        self.select_delay("立即")
        self.assertEqual(self.app.review_state.get(), "已截止")
        self.assertFalse(self.jobs)
        self.assertEqual(self.client.submit.call_count, 1)

    def test_disconnect_edits_and_disabling_revoke_pending_answers(self):
        self.select_delay("10秒")
        self.app.auto_btn.invoke()
        self.release(0)
        self.run_job()
        self.app.editor.insert("end", "修改")
        self.app.edit()
        self.release(1)
        self.run_job()
        self.app.auto_btn.invoke()
        self.app.auto_btn.invoke()
        self.release(2)
        self.run_job()
        self.release(3)  # The generation also spans the disconnection.
        self.app.receive("offline", "演练断开")
        self.app.receive("connected", None)
        self.run_job()
        self.select_delay("立即")
        self.assertFalse(self.jobs)
        self.assertTrue(all(e.pending is None for e in self.app.entries))
        self.client.submit.assert_not_called()

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
        self.client.submit.assert_called_once_with(entry.question, "示例署名\n24小时；60分钟")
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

    def test_undo_wechat_mark_restores_edit_and_requires_fresh_confirmation(self):
        self.app.auto_btn.invoke()
        self.release(0)
        self.run_job()  # A valid draft now has an automatic send queued.
        entry = self.app.current
        self.app.done_btn.invoke()
        self.assertEqual(self.app.done_btn.cget("text"), "撤销微信作答标记")
        self.assertTrue(self.app.send_btn.instate(["disabled"]))
        self.app.done_btn.invoke()
        self.run_job()  # Revoked automatic send is discarded, even after undo.
        self.assertEqual((entry.state, entry.text), ("待确认", "24小时；60分钟"))
        self.assertEqual(self.jobs, [])
        self.client.submit.assert_not_called()

        self.app.editor.delete("1.0", "end")
        self.app.editor.insert("1.0", "自己的答案")
        self.app.edit()
        self.app.done_btn.invoke()
        self.app.done_btn.invoke()
        self.assertEqual(self.app.editor.get("1.0", "end-1c"), "自己的答案")
        self.app.send_btn.invoke()
        button(self.app.active_dialog, "确认提交").invoke()
        self.run_job()
        self.client.submit.assert_called_once_with(entry.question, "示例署名\n自己的答案")
        self.assertEqual((entry.state, entry.receipt["mode"]), ("已提交", "手动"))
        self.assertTrue(self.app.done_btn.instate(["disabled"]))

    def test_undo_while_solving_regenerates_once_without_automatic_send(self):
        self.app.auto_btn.invoke()
        self.release(1)
        self.app.done_btn.invoke()
        self.app.done_btn.invoke()
        self.run_job()  # Discard the generation started before the mark.
        self.assertEqual(self.app.current.text, "")
        self.run_job()
        self.assertEqual((self.app.current.text, self.app.current.state), ("24小时；60分钟", "待确认"))
        self.assertEqual(self.jobs, [])
        self.client.submit.assert_not_called()

    def test_steps_control_applies_to_next_generation(self):
        self.release(2)
        self.assertFalse(self.app.include_steps.get())
        self.app.steps_btn.invoke()
        self.store.assert_called_with("settings.dat", self.app.config)
        with patch("raindesk.solver.ask_gpt", return_value={"items": ["21种"], "explanation": ""}) as model:
            self.run_job()
            self.assertIn("只写结果", model.call_args.args[0])
            self.app.solve_btn.invoke()
            model.return_value["items"] = ["21种。C(7,2)=21"]
            self.run_job()
            self.assertIn("关键算式", model.call_args.args[0])
        self.app.steps_btn.invoke()
        self.assertFalse(self.app.config["include_steps"])
        self.assertEqual(self.app.current.text, "21种。C(7,2)=21")
        self.assertEqual(self.jobs, [])

    def test_deepseek_settings_prompt_import_clear_and_auth_retry(self):
        self.app.settings()
        win = next(widgets(self.root, tk.Toplevel))
        select = next(widgets(win, ttk.Combobox))
        select.set("DeepSeek Harness")
        select.event_generate("<<ComboboxSelected>>")
        self.root.update_idletasks()
        field = next(f for f in widgets(win, ttk.Entry) if "show" in f.keys() and f.cget("show") == "*")
        self.assertEqual(field.master.master.winfo_manager(), "pack")
        with TemporaryDirectory() as directory:
            filename = Path(directory) / ".env"
            filename.write_text("EXAMPLE=other\nDEEPSEEK_API_KEY=example-imported-key\n", encoding="utf-8-sig")
            with patch("raindesk.ui.filedialog.askopenfilename", return_value=str(filename)):
                button(win, "从文件导入…").invoke()
            self.assertEqual(field.get(), "example-imported-key")
            button(win, "保存设置").invoke()
        self.assertEqual(self.app.config["deepseek_api_key"], "example-imported-key")

        self.release(1)
        with patch("raindesk.providers.requests.post", return_value=Mock(status_code=401)):
            self.run_job()
        self.assertEqual(self.app.key_btn.winfo_manager(), "pack")
        self.app.key_btn.invoke()
        win = next(widgets(self.root, tk.Toplevel))
        field = next(f for f in widgets(win, ttk.Entry) if "show" in f.keys() and f.cget("show") == "*")
        field.delete(0, "end")
        button(win, "保存设置").invoke()
        self.assertNotIn("deepseek_api_key", self.app.config)

    def test_remember_model_and_credentials_across_services_and_reopen(self):
        self.app.config.update(model="legacy-gpt", paths={"WorkBuddy": "example.cmd"})

        def open_settings():
            self.app.settings()
            win = next(widgets(self.root, tk.Toplevel))
            provider, model = widgets(win, ttk.Combobox)
            secret = next(f for f in widgets(win, ttk.Entry) if "show" in f.keys() and f.cget("show") == "*")
            return win, provider, model, secret

        def select(widget, name):
            widget.set(name)
            widget.event_generate("<<ComboboxSelected>>")

        win, provider, model, secret = open_settings()
        self.assertEqual(model.get(), "legacy-gpt")
        select(provider, "Claude")
        model.set("example-sonnet")
        secret.insert(0, "example-claude-key")
        select(provider, "Gemini")
        self.assertEqual(secret.get(), "")
        model.set("example-flash")
        secret.insert(0, "example-gemini-key")
        select(provider, "Claude")
        self.assertEqual((model.get(), secret.get()), ("example-sonnet", "example-claude-key"))
        model.set("example-haiku")
        button(win, "保存设置").invoke()
        saved = self.store.call_args.args[1]
        self.assertEqual(saved["model_history"]["Claude"][:2], ["example-haiku", "example-sonnet"])
        self.assertEqual(saved["gemini_api_key"], "example-gemini-key")
        self.assertEqual(saved["paths"]["WorkBuddy"], "example.cmd")

        with patch("raindesk.ui.load_private", return_value=dict(saved)):
            second = App(tk.Tk(), demo=True)
        self.addCleanup(second.close)
        menu = second.model_menu
        menu.invoke("Claude · example-sonnet")
        self.assertEqual(second.config["model_history"]["Claude"][0], "example-sonnet")
        menu.invoke("Gemini · example-flash")
        self.assertEqual((second.config["provider"], second.config["gemini_api_key"]), ("Gemini", "example-gemini-key"))
        self.assertEqual(second.config["claude_api_key"], "example-claude-key")
        self.assertEqual(second.config["model"], "legacy-gpt")

    def test_quick_model_switch_applies_to_next_generation(self):
        self.app.config["claude_api_key"] = "example-claude-key"
        with patch("raindesk.solver.ask_gpt", return_value={"items": ["1"]}) as gpt, \
                patch("raindesk.solver.ask_api", return_value={"items": ["2"]}) as api:
            self.release(0)
            self.app.use_model("Claude", "new-model")
            self.run_job()
            gpt.assert_called_once()
            api.assert_not_called()
            self.release(1)
            self.run_job()
            self.assertEqual(api.call_args.args[0], "Claude")
            self.assertEqual(api.call_args.args[-2:], ("example-claude-key", "new-model"))
        self.assertEqual([e.text for e in self.app.entries], ["1", "2"])
        self.client.submit.assert_not_called()
