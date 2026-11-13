"""Noninteractive integrations with the user's installed desktop AI tools."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


PROVIDERS = ("GPT", "WorkBuddy", "DeepSeek Harness")


class SolverError(Exception):
    pass


def _launcher(provider, configured):
    name = "codebuddy" if provider == "WorkBuddy" else "dsh"
    raw = configured.strip().strip('"') or shutil.which(name + ".exe") or shutil.which(name + ".cmd")
    if not raw:
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


def ask_external(provider, prompt, picture, configured=""):
    wb = provider == "WorkBuddy"
    if provider not in PROVIDERS[1:]:
        raise SolverError("未知解题服务。")
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
        else:
            stdin = prompt
            args = ["headless", "-"]
        try:
            result = subprocess.run(program + args, cwd=directory, input=stdin.encode("utf-8"),
                                    capture_output=True, timeout=150, check=False,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise SolverError(provider + " 未在时限内返回答案。") from exc
        except OSError as exc:
            raise SolverError("无法启动 " + provider + "，请核对程序路径。") from exc
        output = result.stdout.decode("utf-8", errors="replace")
        diag = (result.stderr.decode("utf-8", errors="replace") + output).casefold()
        if result.returncode:
            if any(k in diag for k in ("missing_credential", "api key", "api_key", "unauthorized", "authentication", "login", "登录", "401")):
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
