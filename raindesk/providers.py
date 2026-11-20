"""Noninteractive integrations with DeepSeek and desktop AI tools."""
import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import quote

import requests


@dataclass(frozen=True)
class ApiService:
    url: str
    default_model: str
    key_field: str
    env_names: tuple[str, ...]


API_SERVICES = {
    "DeepSeek Harness": ApiService("https://api.deepseek.com/chat/completions", "deepseek-flash", "deepseek_api_key", ("DEEPSEEK_API_KEY",)),
    "GLM": ApiService("https://open.bigmodel.cn/api/paas/v4/chat/completions", "glm-5v-turbo", "glm_api_key", ("ZHIPUAI_API_KEY", "GLM_API_KEY", "BIGMODEL_API_KEY")),
    "Claude": ApiService("https://api.anthropic.com/v1/messages", "claude-sonnet-4-6", "claude_api_key", ("ANTHROPIC_API_KEY",)),
    "Gemini": ApiService("https://generativelanguage.googleapis.com/v1beta/models/", "gemini-flash-latest", "gemini_api_key", ("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    "OpenAI API": ApiService("https://api.openai.com/v1/chat/completions", "gpt-5-mini", "openai_api_key", ("OPENAI_API_KEY",)),
    "通义千问": ApiService("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions", "qwen3-vl-plus", "qwen_api_key", ("DASHSCOPE_API_KEY",)),
    "Kimi": ApiService("https://api.moonshot.cn/v1/chat/completions", "kimi-k3", "kimi_api_key", ("MOONSHOT_API_KEY", "KIMI_API_KEY")),
    "豆包": ApiService("https://ark.cn-beijing.volces.com/api/v3/chat/completions", "", "doubao_api_key", ("ARK_API_KEY",)),
    "Grok": ApiService("https://api.x.ai/v1/chat/completions", "", "grok_api_key", ("XAI_API_KEY",)),
    "OpenRouter": ApiService("https://openrouter.ai/api/v1/chat/completions", "", "openrouter_api_key", ("OPENROUTER_API_KEY",)),
    "硅基流动": ApiService("https://api.siliconflow.cn/v1/chat/completions", "", "siliconflow_api_key", ("SILICONFLOW_API_KEY",)),
}
CLI_PROVIDERS = ("WorkBuddy", "DeepSeek Harness")
PROVIDERS = ("GPT", "WorkBuddy", *API_SERVICES)


def key_help(provider):
    name = "DeepSeek" if provider == "DeepSeek Harness" else provider.removesuffix(" API")
    return f"请在设置中导入或更新 {name} API 密钥，保存后点「重新解题」。"


DEEPSEEK_KEY_HELP = key_help("DeepSeek Harness")


class SolverError(Exception):
    pass


def parse_api_key(provider, text):
    """Accept a pasted key, a single-key text file or the named value in a .env file."""
    value = text.lstrip("\ufeff").strip()
    names = API_SERVICES[provider].env_names
    pattern = "|".join(map(re.escape, names))
    matches = re.findall(rf"(?m)^[ \t]*(?:export[ \t]+)?(?:{pattern})[ \t]*=[ \t]*(.*)$", value)
    if len(matches) == 1:
        value = re.split(r"[ \t]+#", matches[0], maxsplit=1)[0].strip()
        if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
    if not re.fullmatch(r"[A-Za-z0-9._~+/\-]+={0,2}", value):
        raise ValueError(f"请粘贴单行 API 密钥，或导入含 {names[0]} 的文件。")
    return value


def parse_deepseek_key(text):
    return parse_api_key("DeepSeek Harness", text)


def model_history(settings, provider):
    """The first item is active. An empty ID selects the service default."""
    stored = settings.get("model_history", {}).get(provider)
    if isinstance(stored, list) and stored and all(isinstance(m, str) for m in stored):
        return list(dict.fromkeys(stored))
    return [settings.get("model", "") if provider == "GPT" else ""]


def validate_model(model):
    if model and not re.fullmatch(r"[\w@][\w .:/@+\-]*", model):
        raise ValueError("请填写服务提供的模型 ID 或别名。")
    return model


def _launcher(provider, configured):
    name = "codebuddy" if provider == "WorkBuddy" else "dsh"
    raw = configured.strip().strip('"') or shutil.which(name + ".exe") or shutil.which(name + ".cmd")
    if not raw:
        if provider == "DeepSeek Harness":
            raise SolverError(DEEPSEEK_KEY_HELP)
        raise SolverError("请在设置中选择 %s 的程序或 CLI 路径。" % provider)
    path = Path(raw).expanduser().absolute()
    if not path.is_file():
        raise SolverError("所选 %s 程序不存在。" % provider)
    if path.name.casefold() == "workbuddy.exe":
        path = path.parent / "resources/app.asar.unpacked/cli/dist/codebuddy-lite-wb.mjs"
    if path.name.casefold() == "deepseek harness.exe":
        path = path.parent / "resources/runtime/cli/bin/dsh.cmd"
    if not path.is_file():
        raise SolverError("当前安装中缺少 %s 的 CLI 组件。" % provider)
    if path.suffix.lower() in (".js", ".mjs", ".cjs"):
        node = shutil.which("node.exe")
        if provider == "WorkBuddy":
            bundled = sorted((Path.home() / ".workbuddy/binaries/node/versions").glob("*/node.exe"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            if bundled:
                node = str(bundled[0])
        if not node:
            raise SolverError("这个 CLI 入口需要 Node.js。")
        return [node, str(path)]
    if path.suffix.lower() not in (".exe", ".cmd", ".bat"):
        raise SolverError("请选择桌面应用、CLI 可执行文件或其 JavaScript 入口。")
    return [str(path)]


def _answer_object(text):
    cleaned = str(text or "").strip()
    fenced = re.fullmatch(r"\x60{3}(?:json)?\s*(.*?)\s*\x60{3}", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    try:
        obj = json.loads(cleaned)
    except ValueError as exc:
        raise SolverError("模型未返回可读取的答案格式。") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("items"), list):
        raise SolverError("模型返回的答案缺少 items 字段。")
    return obj


def ask_api(provider, prompt, picture, key, model=""):
    service = API_SERVICES[provider]
    if not key:
        raise SolverError(key_help(provider))
    model = validate_model(model) or service.default_model
    if not model:
        raise SolverError(f"请在设置中填写 {provider} 账户可用的模型或接入点 ID。")
    encoded = base64.b64encode(picture).decode("ascii") if picture else ""
    url = service.url
    if provider == "Claude":
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
        blocks = [{"type": "text", "text": prompt}]
        if encoded:
            blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": encoded}})
        body = {"model": model, "max_tokens": 8192, "messages": [{"role": "user", "content": blocks}]}
    elif provider == "Gemini":
        url += quote(model.removeprefix("models/"), safe="") + ":generateContent"
        headers = {"x-goog-api-key": key}
        parts = [{"text": prompt}]
        if encoded:
            parts.append({"inlineData": {"mimeType": "image/jpeg", "data": encoded}})
        body = {"contents": [{"role": "user", "parts": parts}], "generationConfig": {"responseMimeType": "application/json"}}
    else:
        headers = {"Authorization": "Bearer " + key}
        content = prompt
        if encoded:
            content = [{"type": "text", "text": prompt},
                       {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + encoded}}]
        body = {"model": model, "messages": [{"role": "user", "content": content}]}
        if provider == "DeepSeek Harness":
            body["response_format"] = {"type": "json_object"}
    try:
        response = requests.post(url, headers=headers, json=body, allow_redirects=False, timeout=(10, 140))
    except requests.RequestException:
        raise SolverError(f"无法连接 {provider}，请检查网络后重试。") from None
    status = response.status_code
    if status in (401, 403) or (provider == "Gemini" and status == 400 and "API_KEY_INVALID" in response.text):
        raise SolverError(key_help(provider))
    if status == 402:
        raise SolverError(f"{provider} 余额不足，请检查账户余额。")
    if status == 429:
        raise SolverError(f"{provider} 请求受额度或频率限制，请稍后重新解题。")
    if status in (400, 404):
        raise SolverError(f"{provider} 未接受本次请求（HTTP {status}）。请核对模型 ID，以及该模型是否支持题面图片。")
    if status != 200:
        raise SolverError(f"{provider} 请求失败（HTTP {status}）。")
    try:
        data = response.json()
        if provider == "Claude":
            answer = "".join(part["text"] for part in data["content"] if part.get("type") == "text")
        elif provider == "Gemini":
            parts = data["candidates"][0]["content"]["parts"]
            answer = "".join(part.get("text", "") for part in parts if not part.get("thought"))
        else:
            answer = data["choices"][0]["message"]["content"]
    except (ValueError, IndexError, KeyError, TypeError, AttributeError):
        raise SolverError(f"{provider} 未返回可读取的答案。") from None
    return _answer_object(answer)


def ask_external(provider, prompt, picture, configured="", *, deepseek_key="", model=""):
    wb = provider == "WorkBuddy"
    if provider not in CLI_PROVIDERS:
        raise SolverError("未知解题服务。")
    validate_model(model)
    program = _launcher(provider, configured)
    with tempfile.TemporaryDirectory(prefix="RainDesk-") as directory:
        if wb:
            blocks = [{"type": "text", "text": prompt}]
            if picture:
                blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                            "data": base64.b64encode(picture).decode("ascii")}})
            stdin = json.dumps({"type": "user", "message": {"role": "user", "content": blocks}},
                               ensure_ascii=False) + "\n"
            args = ["-p", "--input-format", "stream-json", "--output-format", "stream-json",
                    "--verbose", "--max-turns", "1"]
            if model:
                args += ["--model", model]
        else:
            stdin = prompt
            args = ["headless"]
            if model:
                overlay = Path(directory) / "model.yml"
                config = {"provider": "deepseek-official", "model": model}
                overlay.write_text(json.dumps([{"id": "agent-default-model", "config": config}]), encoding="utf-8")
                args += ["--patch", str(overlay)]
            args.append("-")
        env = {**os.environ, "DEEPSEEK_API_KEY": deepseek_key} if not wb and deepseek_key else None
        try:
            result = subprocess.run(program + args, cwd=directory, input=stdin.encode("utf-8"),
                                    capture_output=True, timeout=150, check=False, env=env,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise SolverError(provider + " 未在时限内返回答案。") from exc
        except OSError as exc:
            raise SolverError("无法启动 " + provider + "，请核对程序路径。") from exc
        output = result.stdout.decode("utf-8", errors="replace")
        diag = (result.stderr.decode("utf-8", errors="replace") + output).casefold()
        if result.returncode:
            if any(k in diag for k in ("missing_credential", "api key", "api_key", "unauthorized", "authentication", "login", "登录", "401")):
                if not wb:
                    raise SolverError(DEEPSEEK_KEY_HELP)
                raise SolverError(provider + " 需要先在它自己的 CLI 中完成登录或配置 API 密钥。")
            raise SolverError(provider + " 调用失败，请检查其 CLI 配置和网络。")
        if wb:
            for line in reversed(output.splitlines()):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get("type") == "result":
                    if event.get("is_error"):
                        if "authentication required" in diag or "please use /login" in diag:
                            raise SolverError("请在 WorkBuddy 的 CodeBuddy CLI 完成 /login，再重试。")
                        raise SolverError("WorkBuddy 返回错误，请检查 CLI 登录、模型和额度。")
                    return _answer_object(event.get("result"))
            if "authentication required" in diag:
                raise SolverError("请在 WorkBuddy 的 CodeBuddy CLI 完成 /login，再重试。")
            raise SolverError("WorkBuddy 未返回最终结果。")
        return _answer_object(output)
