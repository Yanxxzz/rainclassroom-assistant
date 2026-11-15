"""Chinese typography and subquestion layout for generated answers."""
import re


_HAN = r"\u3400-\u9fff\uf900-\ufaff\U00020000-\U0002fa1f"
_MATH = (r"A-Za-z0-9０-９\u0370-\u03ff\u2070-\u209f\u2100-\u22ff\U0001d400-\U0001d7ff"
         r"²³¹()（）{}\[\]!%％°±+*/=^<>\-")
_GAP = r"[ \u00a0\u2000-\u200a\u202f\u3000]+"
_SPACE = re.compile(
    rf"(?<=[{_HAN}]){_GAP}(?=[{_MATH}])|(?<=[{_MATH}]){_GAP}(?=[{_HAN}])|"
    rf"(?<=[，。；：！？、]){_GAP}(?=\S)|(?<=\S){_GAP}(?=[，。；：！？、])")
_QUOTED = re.compile(r'(\x60{3}[\s\S]*?\x60{3}|\x60[^\x60\n]*\x60|"(?:\\.|[^"\\\n])*"|'
                     r"'(?:\\.|[^'\\\n])*'|“[^”\n]*”|「[^」\n]*」)")
_LITERAL_TASK = re.compile(r"逐字|原样|原封不动|分词|空格.{0,4}(?:分隔|隔开|分开)|"
                          r"(?:保留|包括|包含).{0,3}空格|(?:编写|写出|填写|补全|输出|翻译).{0,12}(?:代码|命令|程序|SQL语句)", re.I)
_LABEL = re.compile(r"^(?:（\s*\d+\s*）\s*(?=[^\s!^=*/+−×÷\-])|"
                    r"\(\s*\d+\s*\)\s*(?=[^\s!^=*/+−×÷(\[\-])|"
                    r"第[一二三四五六七八九十\d]+(?:小题|问|题)[：:、.]\s*|\d+\s*[、）)]\s*|\d+[.．][ \t]+)")
_COUNT_ASK = re.compile(r"多少|几[个种项次人条组本]")


def chinese_spacing(text, stem):
    if _LITERAL_TASK.search(stem):
        return text
    return "".join(piece if i % 2 else _SPACE.sub("", piece)
                   for i, piece in enumerate(_QUOTED.split(text)))


class SubanswerFormatError(ValueError):
    """The draft merged independent answers or left an item empty."""


def join_subanswers(stem, parts):
    if _LITERAL_TASK.search(stem):
        return "；".join(part.strip() for part in parts)
    # Count only explicit, separate quantitative questions. A request to give a
    # result and explain it belongs in one item, and commas inside formulas are data.
    clauses = re.findall(r"[^。！？!?\n]+[？?]", _QUOTED.sub("", stem))
    count = sum(bool(_COUNT_ASK.search(clause)) for clause in clauses)
    if len(parts) == 1:
        separated = [item.strip() for item in re.split(r"[；;]", parts[0])]
        if len(separated) > 1 and (len(separated) == count or all(_LABEL.match(item) for item in separated)):
            parts = separated
    if len(parts) < count:
        raise SubanswerFormatError("每个独立小问各占items的一项，按原顺序给出结果和必要单位。")
    clean = [_LABEL.sub("", part.strip()).strip().rstrip("；;").strip() for part in parts]
    if len(clean) > 1:
        clean = [part.removesuffix("。").rstrip() for part in clean]
    if not all(clean):
        raise SubanswerFormatError("每个小问都需要完整答案，省略小题编号。")
    return "；".join(clean)
