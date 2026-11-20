import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch

from raindesk.local import load_private, save_private
from raindesk.providers import DEEPSEEK_KEY_HELP, SolverError, ask_external, parse_deepseek_key
from raindesk.question import Question
from raindesk.solver import solve


class DeepSeekIntegration(TestCase):
    def setUp(self):
        self.q = Question("demo", "slide", "q", 5, "3乘4等于多少？", (), 1, 1, "", "", 1, None)
        self.settings = {"provider": "DeepSeek Harness", "deepseek_api_key": "example-key"}

    def test_import_text_and_env(self):
        for value in ["example-key\n", "\ufeffOTHER=abc\nexport DEEPSEEK_API_KEY = 'example-key' # test\n",
                      'DEEPSEEK_API_KEY="example-key"\nMODEL=some-model']:
            self.assertEqual(parse_deepseek_key(value), "example-key")
        for invalid in ["", "OTHER=secret-value", "DEEPSEEK_API_KEY=", "one two", "one\ntwo",
                        "DEEPSEEK_API_KEY=x\nDEEPSEEK_API_KEY=y"]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                parse_deepseek_key(invalid)

    @skipUnless(os.name == "nt", "Windows account encryption required")
    def test_windows_settings_roundtrip_keeps_key_encrypted(self):
        with TemporaryDirectory() as directory, patch.dict(os.environ, {"RAINDESK_DATA_DIR": directory}):
            save_private("settings.dat", self.settings)
            self.assertNotIn(b"example-key", (Path(directory) / "settings.dat").read_bytes())
            self.assertEqual(load_private("settings.dat"), self.settings)

    def test_imported_key_alone_calls_official_api_with_text_and_image(self):
        answer = json.dumps({"items": ["12"], "explanation": ""})
        response = Mock(status_code=200)
        response.json.return_value = {"choices": [{"message": {"content": answer}}]}
        with patch("raindesk.providers.requests.post", return_value=response) as post, \
                patch("raindesk.providers.subprocess.run") as cli:
            self.assertEqual(solve(self.q, self.settings, b"image"), ("12", ""))
        url, = post.call_args.args
        req = post.call_args.kwargs
        self.assertEqual(url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(req["headers"]["Authorization"], "Bearer example-key")
        self.assertFalse(req["allow_redirects"])
        content = req["json"]["messages"][0]["content"]
        self.assertIn(self.q.body, content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("example-key", str(req["json"]))
        cli.assert_not_called()

    def test_harness_uses_imported_key_only_for_its_child_process(self):
        result = Mock(returncode=0, stdout=b'{"items":["12"]}', stderr=b"")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "original-example"}), \
                patch("raindesk.providers._launcher", return_value=["example-dsh"]), \
                patch("raindesk.providers.subprocess.run", return_value=result) as run:
            settings = dict(self.settings, paths={"DeepSeek Harness": "example-dsh"})
            self.assertEqual(solve(self.q, settings), ("12", ""))
            cmd, = run.call_args.args
            self.assertEqual(cmd[-2:], ["headless", "-"])
            self.assertNotIn("example-key", str(cmd))
            self.assertNotIn(b"example-key", run.call_args.kwargs["input"])
            self.assertEqual(run.call_args.kwargs["env"]["DEEPSEEK_API_KEY"], "example-key")
            self.assertEqual(os.environ["DEEPSEEK_API_KEY"], "original-example")
            ask_external("DeepSeek Harness", "题面", None)
            self.assertIsNone(run.call_args.kwargs["env"])
            result.stdout = json.dumps({"type": "result", "result": '{"items":["12"]}'}).encode()
            self.assertEqual(solve(self.q, dict(self.settings, provider="WorkBuddy")), ("12", ""))
            self.assertIsNone(run.call_args.kwargs["env"])

    def test_errors_show_usable_messages_without_echoing_secrets(self):
        for status, message in [(401, DEEPSEEK_KEY_HELP), (402, "余额不足"), (429, "稍后"), (503, "503")]:
            with self.subTest(status=status), patch("raindesk.providers.requests.post", return_value=Mock(status_code=status)):
                with self.assertRaises(SolverError) as error:
                    solve(self.q, self.settings)
                self.assertIn(message, str(error.exception))
                self.assertNotIn("example-key", str(error.exception))
        with patch("raindesk.providers.shutil.which", return_value=None):
            with self.assertRaisesRegex(SolverError, "导入"):
                solve(self.q, {"provider": "DeepSeek Harness"})
