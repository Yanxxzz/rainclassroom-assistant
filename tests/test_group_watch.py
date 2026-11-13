import threading
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image
import zxingcpp

from raindesk.client import Lesson
from raindesk.group_panel import GroupPanel
from raindesk.group_watch import GroupWatch, classify, read_codes
from raindesk.ui import App
from raindesk.wechat_window import scan_in_wechat


def qr(text):
    barcode = zxingcpp.create_barcode(text, zxingcpp.BarcodeFormat.QRCode)
    pixels = memoryview(zxingcpp.write_barcode_to_image(barcode, scale=4))
    h, w = pixels.shape
    return Image.frombytes("L", (w, h), pixels.tobytes())


class GroupWindowWorkflow(unittest.TestCase):
    def test_first_frame_deduplication_and_course_link(self):
        wx = "https://weixin.qq.com/q/exampleClass"
        rain = "https://www.yuketang.cn/lesson?lessonId=123"
        blank = Image.new("L", (640, 400), 245)
        frame = blank.copy()
        frame.paste(qr(wx), (10, 15))
        events = []
        watch = GroupWatch(Mock(), lambda kind, value: events.append((kind, value)))
        watch.ingest(frame)
        watch.ingest(blank)
        watch.ingest(frame)
        frame.paste(qr(rain), (320, 190))
        watch.ingest(frame)
        self.assertEqual(events, [("codes", (True, [classify(wx)])), ("codes", (False, [classify(rain)]))])
        hit, other = read_codes(frame)
        self.assertTrue(hit.center[0] < 200 < other.center[0])
        self.assertEqual(other.code.lesson_id, "123")
        self.assertNotIn("exampleClass", repr(hit))
        for url in ("https://other.example/q/exampleClass", "https://weixin.qq.com.evil.test/q/abcdef",
                    "https://weixin.qq.com@evil.test/q/abcdef", "https://weixin.qq.com/r/exampleClass",
                    "https://www.yuketang.cn/lesson?lessonId=1&lessonId=2", "not a URL"):
            self.assertIsNone(classify(url))

    def test_only_the_chosen_live_course_can_connect(self):
        app = Mock(root=tk.Tcl(), lessons=[], entries=[], desk=None, session_busy=False)
        panel = GroupPanel(app)
        watch = panel.watch = Mock()
        panel.automatic = True
        panel.course_id = "wanted"
        code = classify("http://weixin.qq.com/q/exampleNew")
        panel.events.put((watch, "codes", (True, [code])))
        panel.drain()
        watch.request_scan.assert_not_called()
        with patch.object(panel, "poll"):
            panel.events.put((watch, "codes", (False, [code])))
            panel.drain()
        watch.request_scan.assert_called_once_with(code)
        ticket = panel.request
        lesson = Lesson("2", "测试课", "wanted")
        with patch.object(panel, "later") as later:
            panel.match(ticket, [Lesson("1", "测试课", "other")])
            panel.match(ticket, [lesson, Lesson("3", "测试课", "wanted")])
            self.assertEqual(later.call_count, 2)
            app.join_lesson.assert_not_called()
        panel.match(ticket, [Lesson("1", "测试课", "other"), lesson])
        app.join_lesson.assert_called_once_with(lesson, watch_request=ticket)
        self.assertNotIn("已进入课堂", panel.status.get())
        app.client.lesson = lesson
        panel.connected()
        self.assertIn("已进入课堂", panel.status.get())
        panel.stop()
        panel.match(ticket, [lesson])
        app.join_lesson.assert_called_once()

    def test_scanner_only_invokes_exact_item_in_new_same_process_menu(self):
        import uiautomation as auto

        target = Mock(pid=44)
        item = Mock(ControlType=auto.ControlType.MenuItemControl, Name="识别图中二维码")
        unrelated = Mock(ControlType=auto.ControlType.MenuItemControl, Name="转发")
        running = threading.Event()
        running.set()
        with patch.object(auto, "UIAutomationInitializerInThread"), \
             patch("raindesk.wechat_window._right_click", return_value=True), \
             patch("raindesk.wechat_window.handles", side_effect=[[5], [5, 6, 7]]), \
             patch("raindesk.wechat_window.window_pid", side_effect=lambda h: 44 if h == 7 else 55), \
             patch.object(auto, "ControlFromHandle") as root, \
             patch.object(auto, "WalkControl", return_value=[(unrelated, 1), (item, 1)]):
            self.assertTrue(scan_in_wechat(target, Mock(), running.is_set))
            root.assert_called_once_with(7)
            item.GetPattern.return_value.Invoke.assert_called_once()
            unrelated.GetPattern.assert_not_called()

    def test_stale_callbacks_do_not_act_on_a_replacement_window(self):
        app = Mock(root=tk.Tcl(), entries=[], session_busy=False)
        panel = GroupPanel(app)
        old, new = Mock(), Mock()
        panel.watch = new
        panel.automatic = True
        code = classify("http://weixin.qq.com/q/exampleOld")
        panel.events.put((old, "codes", (False, [code])))
        panel.events.put((old, "ended", None))
        panel.drain()
        self.assertIs(panel.watch, new)
        self.assertEqual(panel.codes, {})
        new.request_scan.assert_not_called()

    def test_delayed_new_lesson_does_not_rejoin_the_old_session(self):
        app = Mock(root=tk.Tcl(), entries=[], session_busy=False)
        old = Lesson("10", "课程", "course")
        new = Lesson("11", "课程", "course")
        app.client.lesson = old
        panel = GroupPanel(app)
        panel.watch = Mock()
        panel.course_id = "course"
        with patch.object(panel, "poll"):
            panel.process(classify("https://weixin.qq.com/q/anotherTest"))
        with patch.object(panel, "later") as later:
            panel.match(panel.request, [old])
            later.assert_called_once()
        app.join_lesson.assert_not_called()
        panel.match(panel.request, [old, new])
        app.join_lesson.assert_called_once_with(new, watch_request=panel.request)

    def test_cancelled_automatic_join_preserves_the_open_desk(self):
        app = Mock(entries=[])
        app.group.request = 1
        with patch("raindesk.ui.RainClient") as make_client:
            App.join_lesson(app, Lesson("next", "课程"), watch_request=1)
            run, finish = app.task.call_args.args
            app.group.request = 2
            run()
            finish(None)
            make_client.return_value.enter.assert_not_called()
            app.reset_class.assert_not_called()
            app.monitor.assert_not_called()
