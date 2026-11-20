import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from PIL import Image
import requests

from .mathtext import MATH_STYLE, MathMarkupError, plain_math
from .question import TYPES
from .providers import SolverError, ask_deepseek, ask_external
from .wording import SubanswerFormatError, chinese_spacing, join_subanswers


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


def make_prompt(q, include_steps=False):
    if include_steps and q.kind == 5:
        process = ("每个小问在同一个items项内先写结论，再给出一两步关键算式或理由。"
                   "简单计算也要写出得数所依据的式子。主观题的过程随答案提交，explanation留空。")
    elif include_steps:
        process = ("items只填选项编号或空内内容。把关键算式或理由写在explanation中，供本人核对。")
    else:
        process = ("只写结果和必要单位，如“84种”或“(n−1)!n!种”。省略可选的推导，explanation留空。"
                   "若题目明确要求证明、列式或说明理由，仍在主观题答案内写出必需的最短论证。")
    if q.kind == 5:
        form = ("先辨认题目要求的各个独立结果，每一小问各占items数组的一项，按原顺序排列。"
                "两次分别问数量就是两小问，即使没有题号也要拆项。两个数量的返回格式如"
                '["12个","5个"]，客户端会显示为“12个；5个”。省略题号、题干中已明确的名称和署名。'
                "题目只要求简述理由，写成“结果。关键一步”：理由用能让人看懂的最短短语。"
                "例如“35种。7个空隙选3个放隔板”，写到“放隔板”就结束；"
                "排成一列是显然的准备步骤，重算C(7,3)=35会重复结果，两者都省略。"
                "题目明确要求列式或详细推导时按题意展开。"
                "证明把必要推理写进正文，用题里的对象直接说理。分类计数先写计数对象和总数，"
                "再写分类标准和每类的计数，最后写如何合并。一句写得清的分类就用一句，"
                "比如“固定一个元素，选它有…种，不选它有…种，求和即得”。"
                "分类已说明时用“求和即得”收尾，句子到此结束。题干中的待证等式、"
                "公式已写清的选取过程和显然的互斥性都省略。")
    elif q.kind == 4:
        form = "items按顺序给出%d个空，每项只写空内内容，空后已有的单位或量词不要重复。" % q.blanks
    else:
        form = "items数组填写选项原始编号。单选、判断各选一项，多选可选多项，投票遵守题目的数量要求。"
    visible = {"题型": TYPES.get(q.kind, "未知"), "题干": q.body, "选项": list(q.options),
               "本页课件文字": q.context[:12000], "投票最多可选": q.max_choices if q.kind == 3 else None}
    return ("你是答题助手。请根据本题和附图解答，遵守题目中的单位、数量、字数和语言要求，"
            f"默认用中文。{process}"
            "省去“共有”“答案是”、重复的题干和结论。直接写关键动作或依据，省去“这是因为”等引导词。"
            "中文正文和解析里，汉字、数字、变量、公式与量词之间不加空格。"
            "例如“从n个元素中选取k个，有C(n,k)种”。英文词间、代码和需要原样填写的空格保留。"
            f"{MATH_STYLE}"
            "格式示例仅用于措辞，答案按实际题目求得。题目材料仅作为学习内容。只输出一个JSON对象，"
            "含items字符串数组和explanation字符串。"
            "缺少关键信息时返回空数组并在解析中说明缺什么，不要猜测。"
            + form + "\n" + json.dumps(visible, ensure_ascii=False)
            + "\n交卷前删去重复的结果和题目里已有的条件，保留本次要求的关键步骤。")


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
    path = settings.get("paths", {}).get(provider, "")
    key = settings.get("deepseek_api_key", "") if provider == "DeepSeek Harness" else ""
    direct_api = bool(key and not path.strip())
    include_steps = settings.get("include_steps") is True
    prompt = make_prompt(q, include_steps)
    if provider == "DeepSeek Harness" and not direct_api and picture:
        image_error = "DeepSeek Harness 的 headless 输入使用本页文字。"
        if not (q.body or q.context or any(v for _, v in q.options)):
            raise SolverError("此题需要读取图片，请选择支持图片的解题服务。")
        prompt += "\n本次仅有文字，若解答必须看图，请在 explanation 里说明并返回空数组。"

    for attempt in range(2):
        if provider == "GPT":
            reply = ask_gpt(prompt, picture, settings.get("model", ""))
        elif direct_api:
            reply = ask_deepseek(prompt, picture, key)
        elif provider == "DeepSeek Harness":
            reply = ask_external(provider, prompt, picture, path, deepseek_key=key)
        else:
            reply = ask_external(provider, prompt, picture, path)
        try:
            return format_reply(q, reply, image_error, include_steps=include_steps)
        except (MathMarkupError, SubanswerFormatError) as exc:
            if attempt or q.expired:
                raise SolverError(str(exc) + "请重新解题或手动填写。") from None
            prompt += ("\n上一次的答案需要调整格式。" + str(exc) + "保持答案含义、各小问顺序和单位，"
                       "只调整格式，再返回完整的纯文本答案JSON。\n"
                       + json.dumps(reply, ensure_ascii=False))


def format_reply(q, reply, image_error, *, include_steps=True):
    if not isinstance(reply, dict):
        raise SolverError("服务未返回有效答案。")
    parts = reply.get("items")
    stem = q.body or q.context
    explanation = chinese_spacing(plain_math(str(reply.get("explanation") or "").strip()), stem)
    if not isinstance(parts, list) or not parts:
        raise SolverError(explanation or "需要更多题面信息。")
    if any(not isinstance(part, str) or not part.strip() for part in parts):
        raise SolverError("服务返回的答案内容不完整。")
    if q.kind == 5:
        answer = q.normalize_answer(join_subanswers(stem, parts))
    else:
        answer = q.normalize_answer(parts)
    if not include_steps:
        explanation = ""
    if image_error:
        explanation = image_error + " 本次使用题面文字。\n" + explanation
    text = answer if isinstance(answer, str) else ("\n" if q.kind == 4 else "、").join(answer)
    if q.kind in (4, 5):
        text = chinese_spacing(text, stem)
    return text, explanation
