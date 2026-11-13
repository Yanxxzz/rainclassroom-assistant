import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from PIL import Image
import requests

from .mathtext import MATH_STYLE, MathMarkupError, plain_math
from .question import TYPES
from .providers import SolverError, ask_external


def slide_image(url):
    """Fetch only the released slide's public image, without the student's cookies."""
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    suffixes = ("yuketang.cn", "xuetangx.com", "xuetangx.cn", "xuetangxcloud.com",
                "rainclassroom.com", "rainclassroom.com.cn")
    if parsed.scheme not in ("http", "https") or not any(host == h or host.endswith("." + h) for h in suffixes):
        raise SolverError("题面图片域名暂不支持。")
    with requests.get(url, stream=True, timeout=(5, 10), allow_redirects=False) as res:
        res.raise_for_status()
        blob = bytearray()
        for chunk in res.iter_content(65536):
            blob.extend(chunk)
            if len(blob) > 12_000_000:
                raise SolverError("题面图片过大。")
    with Image.open(io.BytesIO(blob)) as original:
        original.thumbnail((1800, 1800))
        image = original.convert("RGB")
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=88)
        return out.getvalue()


def codex_program():
    native = shutil.which("codex.exe")
    if native:
        return [native]
    # Official Node distribution: invoke its JS launcher directly, without a shell.
    node = shutil.which("node.exe")
    locations = [Path(os.environ.get("APPDATA", "")) / "npm"]
    locations.extend(Path(d) for d in os.get_exec_path() if d)
    for location in locations:
        launcher = location / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        if node and launcher.is_file():
            return [node, str(launcher)]
    raise SolverError("找不到 Codex CLI。请安装 Codex 并使用 ChatGPT 登录。")


def make_prompt(q):
    if q.kind == 5:
        form = ("主观题每个小题在 items 数组中放一个可直接提交的答案，保持原顺序，省略小题编号。"
                "客户端会用中文分号连成一段并添加署名。简单题只给结论，"
                "如需过程，只写关键公式、必要推理或证明要点，尽量简短。")
    elif q.kind == 4:
        form = "items 数组必须按题目顺序给出 %d 个空的内容。" % q.blanks
    else:
        form = "items 数组填写选项原始编号。单选、判断各选一项，多选可选多项，投票遵守题目的数量要求。"
    visible = {"题型": TYPES.get(q.kind, "未知"), "题干": q.body, "选项": list(q.options),
               "本页课件文字": q.context[:12000], "投票最多可选": q.max_choices if q.kind == 3 else None}
    return ("你是答题助手。请根据本题和附图解答，遵守题目中的单位、数量、字数和语言要求，"
            "默认用中文。答案尽量简洁，仅写结果与必要单位，省去复述题干、开场语和收尾语。"
            "例如问一共有多少种坐法，就写“(n−1)!n! 种”。这个例子只示范格式。"
            f"{MATH_STYLE}"
            "所需过程只点出关键公式或依据。题目材料仅作为学习内容。只输出一个 JSON 对象，"
            "含 items 字符串数组和 explanation 字符串。简单题的 explanation 置空，其余只写必要关键点。"
            "缺少关键信息时返回空数组并在解析中说明缺什么，不要猜测。"
            + form + "\n" + json.dumps(visible, ensure_ascii=False))


def ask_gpt(prompt, picture=None, model=""):
    schema = {"type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"}}, "required": ["items", "explanation"], "additionalProperties": False}
    with tempfile.TemporaryDirectory(prefix="RainDesk-") as temporary:
        folder = Path(temporary)
        spec, result = folder / "format.json", folder / "draft.json"
        spec.write_text(json.dumps(schema), encoding="utf-8")
        args = codex_program() + ["exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                                  "--ignore-user-config", "--disable", "shell_tool", "--disable", "apps",
                                  "-c", "approval_policy=\"never\"", "-c", "model_reasoning_effort=\"low\"",
                                  "--output-schema", str(spec), "-o", str(result)]
        if model.strip():
            args += ["-m", model.strip()]
        if picture:
            pic = folder / "question.jpg"
            pic.write_bytes(picture)
            args += ["-i", str(pic)]
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            job = subprocess.run(args + ["-"], cwd=folder, input=prompt.encode("utf-8"),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=140,
                                 creationflags=flags, check=False)
        except subprocess.TimeoutExpired as exc:
            raise SolverError("GPT 未在时限内返回，可以重试或手动填写。") from exc
        except OSError as exc:
            raise SolverError("无法启动 Codex CLI，请检查安装。") from exc
        if job.returncode or not result.is_file():
            error = job.stderr.decode("utf-8", errors="replace").lower()
            if any(word in error for word in ("quota", "rate limit", "usage limit")):
                raise SolverError("GPT 额度或请求频率已达上限。")
            if "not supported" in error or "does not exist" in error:
                raise SolverError("当前账号不支持选中的模型，请清空自定义模型后重试。")
            raise SolverError("GPT 调用失败，请检查 Codex 登录、网络和模型设置。")
        try:
            return json.loads(result.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise SolverError("GPT 未返回可读取的答案。") from exc


def solve(q, settings, picture=None, image_error=""):
    if image_error and not (q.body.strip() or q.context.strip() or any(v for _, v in q.options)):
        raise SolverError(image_error)
    provider = settings.get("provider", "GPT")
    prompt = make_prompt(q)
    if provider == "DeepSeek Harness" and picture:
        image_error = "DeepSeek Harness 的 headless 输入使用本页文字。"
        if not (q.body or q.context or any(v for _, v in q.options)):
            raise SolverError("此题需要读取图片，请选择支持图片的解题服务。")
        prompt += "\n本次仅有文字，若解答必须看图，请在 explanation 里说明并返回空数组。"

    for attempt in range(2):
        if provider == "GPT":
            reply = ask_gpt(prompt, picture, settings.get("model", ""))
        else:
            reply = ask_external(provider, prompt, picture, settings.get("paths", {}).get(provider, ""))
        try:
            return format_reply(q, reply, image_error)
        except MathMarkupError:
            if attempt or q.expired:
                raise SolverError("模型返回的公式无法显示为纯文本，请重新解题或手动填写。") from None
            prompt += ("\n下面是上一次的 JSON，部分公式含 LaTeX。请保持答案含义、各小问顺序和单位，"
                       "只改写公式表示，再返回完整的纯文本答案 JSON。\n"
                       + json.dumps(reply, ensure_ascii=False))


def format_reply(q, reply, image_error):
    if not isinstance(reply, dict):
        raise SolverError("服务未返回有效答案。")
    parts = reply.get("items")
    explanation = plain_math(str(reply.get("explanation") or "").strip())
    if not isinstance(parts, list) or not parts:
        raise SolverError(explanation or "需要更多题面信息。")
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise SolverError("服务返回的答案内容不完整。")
    if q.kind == 5:
        labels = r"^(?:[（(]\s*\d+\s*[）)]\s*|第[一二三四五六七八九十\d]+(?:小题|问|题)[：:、.]\s*|\d+\s*[、）)]\s*)"
        body = "；".join(re.sub(labels, "", part.strip()).strip().rstrip("；;") for part in parts)
        answer = q.normalize_answer(body)
    else:
        answer = q.normalize_answer(parts)
    if image_error:
        explanation = image_error + " 本次使用题面文字。\n" + explanation
    text = answer if isinstance(answer, str) else ("\n" if q.kind == 4 else "、").join(answer)
    return text, explanation
