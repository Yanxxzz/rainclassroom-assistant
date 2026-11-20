import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import Mock, patch
from urllib.parse import urlsplit

from requests import Response

from raindesk.local import load_private, save_private
from raindesk.providers import API_SERVICES, SolverError, ask_external, key_help, model_history, parse_api_key
from raindesk.question import Question
from raindesk.solver import solve


class ModelRoutes(TestCase):
    def setUp(self):
        self.question = Question("demo", "deck", "question", 5, "2乘3是多少？", (), 1, 1, "", "", 1, None)
        self.settings = {service.key_field: f"example-key-{i}" for i, service in enumerate(API_SERVICES.values())}

    @skipUnless(os.name == "nt", "Windows account encryption required")
    def test_all_model_preferences_roundtrip_in_encrypted_settings(self):
        state = dict(self.settings, provider="Claude", submit_delay_seconds=37,
                     model_history={"Claude": ["custom-model", ""], "GPT": ["previous-model"]})
        with TemporaryDirectory() as tmp, patch.dict(os.environ, {"RAINDESK_DATA_DIR": tmp}):
            save_private("settings.dat", state)
            self.assertEqual(load_private("settings.dat"), state)
            ciphertext = (Path(tmp) / "settings.dat").read_bytes()
            self.assertNotIn(b"example-key", ciphertext)
            self.assertNotIn(b"custom-model", ciphertext)

    def test_all_providers_send_only_the_selected_key_model_and_slide(self):
        result = '{"items":["6"],"explanation":""}'
        for provider, service in API_SERVICES.items():
            key = self.settings[service.key_field]
            model = "models/sample-model" if provider == "Gemini" else "sample-model"
            settings = dict(self.settings, provider=provider, model_history={provider: [model]})

            def exchange(request, **options):
                body = json.loads(request.body)
                self.assertEqual(urlsplit(request.url).netloc, urlsplit(service.url).netloc)
                self.assertNotIn(key, request.url)
                self.assertNotIn(key, str(body))
                self.assertFalse(options["allow_redirects"])
                credentials = [value for name, value in request.headers.items()
                               if name.lower() in ("authorization", "x-api-key", "x-goog-api-key")]
                self.assertEqual(len(credentials), 1)
                if provider == "Claude":
                    self.assertEqual(request.headers["x-api-key"], key)
                    self.assertEqual(request.headers["anthropic-version"], "2023-06-01")
                    self.assertEqual(body["model"], model)
                    parts = body["messages"][0]["content"]
                    self.assertIn(self.question.body, parts[0]["text"])
                    self.assertEqual(parts[1]["source"]["data"], "aW1hZ2U=")
                    data = {"content": [{"type": "thinking", "thinking": "ignore"}, {"type": "text", "text": result}]}
                elif provider == "Gemini":
                    self.assertEqual(request.headers["x-goog-api-key"], key)
                    self.assertTrue(request.url.endswith("/models/sample-model:generateContent"))
                    parts = body["contents"][0]["parts"]
                    self.assertIn(self.question.body, parts[0]["text"])
                    self.assertEqual(parts[1]["inlineData"], {"mimeType": "image/jpeg", "data": "aW1hZ2U="})
                    data = {"candidates": [{"content": {"parts": [{"text": "ignore", "thought": True}, {"text": result}]}}]}
                else:
                    self.assertEqual(request.headers["Authorization"], "Bearer " + key)
                    self.assertEqual(body["model"], model)
                    parts = body["messages"][0]["content"]
                    self.assertIn(self.question.body, parts[0]["text"])
                    self.assertEqual(parts[1]["image_url"]["url"], "data:image/jpeg;base64,aW1hZ2U=")
                    data = {"choices": [{"message": {"reasoning_content": "ignore", "content": result}}]}
                response = Response()
                response.status_code = 200
                response._content = json.dumps(data).encode()
                return response

            with self.subTest(provider=provider), patch("requests.sessions.Session.send", side_effect=exchange) as send:
                self.assertEqual(solve(self.question, settings, picture=b"image"), ("6", ""))
                self.assertEqual(send.call_count, 1)

    def test_keys_from_shared_env_file_stay_separate_and_errors_name_service(self):
        env = "\n".join(f"{service.env_names[0]}={self.settings[service.key_field]}" for service in API_SERVICES.values())
        for provider, service in API_SERVICES.items():
            with self.subTest(provider=provider):
                self.assertEqual(parse_api_key(provider, env), self.settings[service.key_field])
                settings = dict(self.settings, provider=provider, model_history={provider: ["sample-model"]})
                with patch("raindesk.providers.requests.post", return_value=Mock(status_code=401)):
                    with self.assertRaises(SolverError) as error:
                        solve(self.question, settings)
                    self.assertEqual(str(error.exception), key_help(provider))
                if not service.default_model:
                    with patch("raindesk.providers.requests.post") as send, self.assertRaisesRegex(SolverError, "模型或接入点"):
                        solve(self.question, dict(settings, model_history={}))
                    send.assert_not_called()

    def test_different_cli_model_defaults_and_overrides(self):
        self.assertEqual(model_history({"model": "old-gpt"}, "GPT"), ["old-gpt"])
        self.assertEqual(model_history({"model": "old-gpt"}, "WorkBuddy"), [""])
        with patch("raindesk.solver.ask_gpt", return_value={"items": ["6"]}) as gpt:
            solve(self.question, {"model": "old-gpt", "model_history": {"GPT": ["chosen-gpt", "old-gpt"]}})
            self.assertEqual(gpt.call_args.args[-1], "chosen-gpt")

        def run(command, **kwargs):
            if command[1] == "headless":
                self.assertEqual(command[-1], "-")
                overlay = Path(command[command.index("--patch") + 1])
                self.assertEqual(overlay.parent, Path(kwargs["cwd"]))
                self.assertEqual(json.loads(overlay.read_text(encoding="utf-8")), [{"id": "agent-default-model",
                                 "config": {"provider": "deepseek-official", "model": "example-model"}}])
                stdout = b'{"items":["6"]}'
            else:
                self.assertEqual(command[command.index("--model") + 1], "example-model")
                stdout = json.dumps({"type": "result", "result": '{"items":["6"]}'}).encode()
            return Mock(returncode=0, stdout=stdout, stderr=b"")

        with patch("raindesk.providers._launcher", return_value=["cli"]), patch("raindesk.providers.subprocess.run", side_effect=run):
            for p in ("WorkBuddy", "DeepSeek Harness"):
                self.assertEqual(ask_external(p, "示例", None, model="example-model")["items"], ["6"])
