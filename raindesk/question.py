from dataclasses import dataclass, replace
from html.parser import HTMLParser
import re
import time

from .mathtext import plain_math


TYPES = {1: "单选", 2: "多选", 3: "投票", 4: "填空", 5: "主观题", 6: "判断"}


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("br", "p", "div", "li", "tr"):
            self.parts.append("\n")
        if tag == "img" and dict(attrs).get("alt"):
            self.parts.append(dict(attrs)["alt"])

    def handle_endtag(self, tag):
        if tag in ("p", "div", "li", "tr"):
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = _Text()
    parser.feed(str(value or ""))
    parser.close()
    return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())


def seconds(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0


def event_time(value):
    stamp = seconds(value)
    return stamp / 1000 if stamp > 10**11 else stamp


@dataclass(frozen=True)
class Question:
    lesson_id: str
    presentation_id: str
    id: str
    kind: int
    body: str
    options: tuple
    blanks: int
    max_choices: int
    image_url: str
    context: str
    opened: float
    deadline: float | None
    from_history: bool = False

    @classmethod
    def from_slide(cls, lesson_id, pres, slide, release, *, from_history=False):
        p = slide["problem"]
        options = tuple((str(o["key"]), plain(o.get("value"))) for o in (p.get("options") or [])
                        if isinstance(o, dict) and o.get("key") is not None)
        context = "\n".join(plain(s["Text"]) for s in slide.get("shapes") or []
                            if isinstance(s, dict) and s.get("Text"))
        opened = event_time(release.get("dt")) or time.time()
        limit = seconds(release.get("limit"))
        return cls(str(lesson_id), str(pres), str(p["problemId"]), int(p.get("problemType") or 0),
                   plain(p.get("body")), options, max(1, len(p.get("blanks") or [])),
                   int(p.get("pollingCount") or 1), slide.get("cover") or slide.get("coverAlt") or "",
                   context, opened, opened + limit if limit > 0 else None, from_history)

    @property
    def key(self):
        return (self.lesson_id, self.id)

    @property
    def expired(self):
        return self.deadline is not None and time.time() >= self.deadline

    def close(self):
        return replace(self, deadline=min(time.time(), self.deadline or time.time()))

    def display_text(self):
        stem = self.body or self.context or "题面见课件图片"
        return "\n".join([stem, *(k + ". " + v for k, v in self.options)])

    def normalize_answer(self, value):
        """Validate model output and the student's edits with the same rules."""
        if self.kind == 5:
            if not isinstance(value, str) or not value.strip():
                raise ValueError("请输入主观题答案。")
            return plain_math(value.strip())
        if self.kind == 4:
            parts = value.splitlines() if isinstance(value, str) else value
            if not isinstance(parts, (list, tuple)) or len(parts) != self.blanks:
                raise ValueError("请按顺序填写 %d 个空，每行一个。" % self.blanks)
            if any(not isinstance(x, str) or not x.strip() for x in parts):
                raise ValueError("每个空都需要填写。")
            return [plain_math(x.strip()) for x in parts]
        if self.kind not in (1, 2, 3, 6):
            raise ValueError("暂不支持这个题型。")
        allowed = {k.casefold(): k for k, _ in self.options}
        if isinstance(value, str):
            value = [s for s in re.split(r"[\s,，、;；]+", value.strip()) if s]
        if not isinstance(value, (tuple, list)) or not value:
            raise ValueError("请选择题目提供的选项。")
        selected = []
        for item in value:
            key = allowed.get(str(item).strip().casefold())
            if key is None:
                raise ValueError("无效选项。可选项为 " + "、".join(allowed.values()))
            if key not in selected:
                selected.append(key)
        maximum = self.max_choices if self.kind == 3 else 1 if self.kind in (1, 6) else len(allowed)
        if len(selected) > maximum:
            raise ValueError("这道题最多选择 %d 项。" % maximum)
        return [k for k, _ in self.options if k in selected]

    def for_submission(self, draft, signature):
        result = self.normalize_answer(draft)
        if self.kind != 5:
            return result
        signature = signature.strip()
        if not signature or "\n" in signature or "\r" in signature:
            raise ValueError("请先在设置中填写一行姓名和学号。")
        while result.startswith(signature):
            result = result[len(signature):].lstrip()
        if not result:
            raise ValueError("请在署名后填写主观题答案。")
        return signature + "\n" + result
