import unittest
from unittest.mock import patch

from raindesk.providers import CLI_PROVIDERS, PROVIDERS, SolverError
from raindesk.question import Question
from raindesk.solver import format_reply, solve


def question(body, kind=5, **fields):
    problem = {"problemId": "sample", "problemType": kind, "body": body, **fields}
    return Question.from_slide("sample", "deck", {"problem": problem}, {})


def draft(q, *items, why=""):
    return format_reply(q, {"items": list(items), "explanation": why}, "")


class AnswerWording(unittest.TestCase):
    def test_merged_quantities_are_reformatted_by_each_provider(self):
        q = question("有多少个函数？其中有多少个单射？")
        replies = [{"items": ["125个函数，60个单射"]}, {"items": ["125 个", "60 个"]}]
        for provider in PROVIDERS:
            ask = "ask_gpt" if provider == "GPT" else ("ask_external" if provider in CLI_PROVIDERS else "ask_api")
            with self.subTest(provider=provider), patch("raindesk.solver." + ask, side_effect=replies) as model:
                self.assertEqual(solve(q, {"provider": provider}), ("125个；60个", ""))
                self.assertEqual(model.call_count, 2)
                self.assertIn("每个独立小问", model.call_args.args[0 if provider == "GPT" else 1])
        with patch("raindesk.solver.ask_gpt", return_value=replies[0]) as model:
            with self.assertRaisesRegex(SolverError, "每个独立小问"):
                solve(q, {})
            self.assertEqual(model.call_count, 2)

    def test_layout_preserves_formulas_and_removes_only_labels(self):
        q = question("总数有多少？其余有多少？")
        self.assertEqual(draft(q, "（1）125 个。;（2）60 个。")[0], "125个；60个")
        self.assertEqual(draft(q, "1. C(n,k)", "2. C(n,k−1)")[0], "C(n,k)；C(n,k−1)")
        q = question("写出两个表达式。")
        self.assertEqual(draft(q, "（1）1.25；（2）(2)!")[0], "1.25；(2)!")
        self.assertEqual(draft(q, "(1)", "(2)(x+1)")[0], "(1)；(2)(x+1)")

    def test_reasons_and_proofs_keep_their_sentences(self):
        q = question("有多少种分法？请简述理由。")
        self.assertEqual(draft(q, "56 种。 8 个空隙选 3 个放隔板")[0], "56种。8个空隙选3个放隔板")
        proof = "从 n 个元素中选取 k 个，有 C(n,k) 种。固定一个元素，分两类，求和即得"
        self.assertEqual(draft(question("用组合方法证明。"), proof)[0],
                         "从n个元素中选取k个，有C(n,k)种。固定一个元素，分两类，求和即得")

    def test_fill_choice_and_significant_spaces(self):
        q = question("计算两空：____种，气温____。", kind=4, blanks=[{}, {}])
        fill, _ = draft(q, "(n−1)(n−2)/2", "-3 摄氏度")
        self.assertEqual(q.for_submission(fill, "示例署名"), ["(n−1)(n−2)/2", "-3摄氏度"])
        q = question("选择术语。", kind=1, options=[{"key": "A", "value": "Open set"}])
        self.assertEqual(draft(q, "a", why="选项 A 的英文是 open set。"), ("A", "选项A的英文是open set。"))
        self.assertEqual(draft(question("求温度。"), "约 -3 度")[0], "约-3度")
        self.assertEqual(draft(question("引文是什么？"), "引文为“第 1 章”")[0], "引文为“第 1 章”")
        self.assertEqual(draft(question("原样输出文本。"), "第 1 章;")[0], "第 1 章;")
        self.assertEqual(draft(question("写出SQL语句。"), "SELECT '第 1 章';")[0], "SELECT '第 1 章';")
        self.assertEqual(draft(question("解释这段话：“有多少组？有多少个？”"), "这是 2 个计数问题")[0],
                         "这是2个计数问题")
