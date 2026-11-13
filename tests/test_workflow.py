from pathlib import Path
import sys
import time
import unittest
from unittest.mock import Mock, patch

from requests.structures import CaseInsensitiveDict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from raindesk.client import Lesson, RainClient, RainError
from raindesk.desk import Desk
from raindesk.live import LessonEvents
from raindesk.question import Question
from raindesk.providers import PROVIDERS, SolverError
from raindesk.solver import make_prompt, solve


def exercise(kind=5, pid="q2"):
    return Question("class", "deck", pid, kind, "分别回答两小问。", (("A", "甲"), ("B", "乙")),
                    2, 1, "", "", time.time(), None)


def classroom():
    client = RainClient()
    client.uid, client.bearer = "student", "lesson-access"
    client.lesson = Lesson("class", "测试课")
    return client


def response(obj, status=200):
    value = Mock(status_code=status, ok=status < 400, headers=CaseInsensitiveDict())
    value.json.return_value = obj
    return value


class ClassroomWorkflow(unittest.TestCase):
    def test_release_review_edit_confirm_then_reconnect(self):
        client = classroom()
        desk = Desk(client, {"signature": "测试同学0000"}, remember=False)
        desk.online = True
        shown = []
        now = int(time.time() * 1000)
        slides = [
            {"problem": {"problemId": "future", "problemType": 5, "body": "尚未发布"}},
            {"problem": {"problemId": "q2", "problemType": 5, "body": "一小时几分钟？"}},
        ]
        def route(method, url, **kwargs):
            if method == "GET":
                return response({"code": 0, "data": {"slides": slides}})
            self.assertEqual(kwargs["json"]["result"]["content"], "测试同学0000\n60 分钟")
            return response({"code": 0, "msg": "ok", "data": {}})
        def emit(kind, payload):
            if kind == "question":
                shown.append(desk.add(payload))
        sock = LessonEvents(client, emit)
        with patch.object(client.http, "request", side_effect=route) as http:
            sock.handle({"op": "hello", "presentation": "deck", "timeline": []})
            sock.handle({"op": "slidenav", "slide": {"si": 1}})
            self.assertEqual(shown, [])
            msg = {"op": "unlockproblem", "problem": {"prob": "q2", "limit": 60, "dt": now}}
            sock.handle(msg)
            e, = shown
            e.text = "60"
            approved = desk.preview(e)
            e.text = "60 分钟"
            with self.assertRaisesRegex(ValueError, "重新确认"):
                desk.confirm(e, approved)
            self.assertEqual([c.args[0] for c in http.call_args_list], ["GET"])
            desk.confirm(e, desk.preview(e))
            self.assertEqual(e.state, "已提交")
            self.assertEqual(e.receipt["code"], 0)
            self.assertEqual(e.receipt["result"], "测试同学0000\n60 分钟")
            sock.handle({"op": "hello", "presentation": "deck", "timeline": [
                {"type": "problem", **msg["problem"]}]})
            self.assertEqual(len(shown), 1)
            with self.assertRaises(ValueError):
                desk.confirm(e, approved)
            self.assertEqual([c.args[0] for c in http.call_args_list], ["GET", "POST"])

    def test_unknown_or_failed_receipt_is_not_success(self):
        for obj, state in [({"code": 3, "msg": "时间已到"}, "待确认"),
                           ({"unexpected": "ok"}, "提交待核对")]:
            client = classroom()
            desk = Desk(client, {"signature": "测试000"}, remember=False)
            desk.online = True
            e = desk.add(exercise())
            e.text = "42"
            with patch.object(client.http, "request", return_value=response(obj)) as transport:
                with self.assertRaises((RainError, ValueError)):
                    desk.confirm(e, desk.preview(e))
                self.assertEqual(e.state, state)
                self.assertEqual(transport.call_count, 1)

    def test_required_format_for_each_question_type(self):
        self.assertEqual(exercise(4).for_submission("12\n24", "测试000"), ["12", "24"])
        for kind in (1, 2, 3, 6):
            self.assertEqual(exercise(kind).for_submission("a", "测试000"), ["A"])
        q = exercise()
        suggestion = {"items": ["（1）24 小时", "（2）60 分钟"], "explanation": ""}
        with patch("raindesk.solver.ask_gpt", return_value=suggestion):
            draft, why = solve(q, {})
        self.assertEqual(draft, "24 小时；60 分钟")
        self.assertEqual(why, "")
        self.assertEqual(q.for_submission("测试000\n" + draft, "测试000"), "测试000\n24 小时；60 分钟")
        self.assertNotIn("测试000", make_prompt(q))

    def test_expiry_and_session_changes_cancel_approval(self):
        client = classroom()
        desk = Desk(client, {"signature": "测试000"}, remember=False)
        desk.online = True
        e = desk.add(exercise())
        e.text = "20"
        approved = desk.preview(e)
        e.question = e.question.close()
        with self.assertRaises(ValueError):
            desk.confirm(e, approved)
        e.question = exercise()
        client.lesson = Lesson("new-class", "下一节课")
        with self.assertRaises(ValueError):
            desk.confirm(e, approved)

    def test_plain_math_drafts_and_explanations_for_all_providers(self):
        q = exercise()
        initial = {"items": [r"（1）\(\frac{a+b}{c+d}\)", r"（2）\binom{n}{k}"], "explanation": ""}
        rewritten = {"items": ["（1）(a+b)/(c+d)", "（2）C(n,k)"], "explanation": r"由 \(x^{2}≥0\)。"}
        for provider in PROVIDERS:
            ask = "ask_gpt" if provider == "GPT" else "ask_external"
            with self.subTest(provider=provider), patch("raindesk.solver." + ask,
                                                       side_effect=[initial, rewritten]) as model:
                draft, why = solve(q, {"provider": provider})
                self.assertEqual(draft, "(a+b)/(c+d)；C(n,k)")
                self.assertEqual(why, "由 x²≥0。")
                prompts = [c.args[0 if provider == "GPT" else 1] for c in model.call_args_list]
                self.assertEqual(len(prompts), 2)
                self.assertIn("纯文本", prompts[0])
                self.assertIn("上一次", prompts[1])
                self.assertEqual(q.for_submission(draft, "示例署名"), "示例署名\n" + draft)

    def test_plain_math_previews_keep_blanks_and_literal_text(self):
        q = exercise(4)
        self.assertEqual(q.for_submission("$x^2$\n$$a+b$$", "示例署名"), ["x²", "a+b"])
        self.assertEqual(exercise().for_submission(r"\[(n−1)!n!\] 种", "示例"),
                         "示例\n(n−1)!n! 种")
        for plain in ("$5、$10", "a_name.txt", r"C:\temp\test", r"\\server\share\pi", r"\d+",
                      "{x | x ≥ 0}", "x^(n+1)", "√(a/b)"):
            with self.subTest(plain=plain):
                self.assertEqual(exercise().normalize_answer(plain), plain)
        self.assertEqual(exercise(1).for_submission("a", "示例"), ["A"])

    def test_residual_markup_never_reaches_a_submission(self):
        q = exercise()
        for raw in (r"\frac{a+b}{c+d}", r"\begin{pmatrix}a&b\\c&d\end{pmatrix}",
                    r"\hat{x}", r"n_{k+1}", r"\mathrm{kg}", r"\(x", "$x+1", "$ x^2 $",
                    r"\\frac{1}{2}"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, "纯文本"):
                q.for_submission(raw, "示例")
        invalid = {"items": ["42"], "explanation": r"由 \sum_{i=1}^n i。"}
        with patch("raindesk.solver.ask_gpt", return_value=invalid) as model:
            with self.assertRaisesRegex(SolverError, "纯文本"):
                solve(q, {})
            self.assertEqual(model.call_count, 2)


if __name__ == "__main__":
    unittest.main()
