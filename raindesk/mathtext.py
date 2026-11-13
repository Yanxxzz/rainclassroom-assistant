"""Plain-text math for answer drafts, explanations and submission previews."""
import re


MATH_STYLE = ("答案和解析中的公式一律写成可直接复制的纯文本，例如 x²、(a+b)/(c+d)、"
              "√(x+1)、C(n,k)、x^(n+1)。保留运算顺序和必要括号，"
              "不要使用 LaTeX 命令、数学定界符或公式代码块。")


class MathMarkupError(ValueError):
    def __init__(self):
        super().__init__("请用纯文本写公式，例如 x² 或 (a+b)/(c+d)。")


_DELIMITED = re.compile(
    r"\\\((.*?)\\\)|\\\[(.*?)\\\]|\$\$(.*?)\$\$|"
    r"(?<![\\$])\$(?![\s$])([^$\n]*?[^\s$])\$(?![\d$])", re.DOTALL)
_WINDOWS_PATH = re.compile(r"(?:\b[A-Za-z]:\\|\\\\[\w.-]+\\[\w.-]+)[^\s，。；！？、\"<>|]*")
_MARKUP = re.compile(
    r"\\[()[\]]|(?<!\\)\$\$|(?:\x60{3}|~{3})\s*(?:la)?tex\b|"
    r"(?<![\\$])\$(?=[A-Za-z\\(])|\$[ \t]+[A-Za-z][^$\n]*?\$|"
    r"[_^]\s*\{|\\[A-Za-z]+\s*(?=\{)|"
    r"(?<![A-Za-z0-9_:/\\])\\[A-Za-z]{2,}|"
    r"\\(?:times|cdot|div|pm|mp|leq?|geq?|neq?|approx|equiv|in|notin|cup|cap|"
    r"to|rightarrow|infty|sqrt|d?frac|tfrac|binom|sum|prod|int|sin|cos|tan|log|"
    r"alpha|beta|gamma|delta|theta|lambda|mu|pi|sigma|omega)(?![A-Za-z])",
    re.IGNORECASE)
_SUPERSCRIPT = str.maketrans("0123456789+-", "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻")


def plain_math(text):
    def unwrap(match):
        expression = next(group for group in match.groups() if group is not None).strip()
        # Only unwrap self-contained plain expressions. Let the provider rewrite
        # structural TeX (fractions, arrays, grouping) without losing precedence.
        expression = re.sub(r"\^\{([+-]?[0-9]+)\}|\^([0-9])(?![0-9])",
                            lambda m: (m[1] or m[2]).translate(_SUPERSCRIPT), expression)
        if not expression or re.search(r"[\\{}&$]", expression):
            raise MathMarkupError()
        return expression

    text = _DELIMITED.sub(unwrap, text)
    if _MARKUP.search(_WINDOWS_PATH.sub("", text)):
        raise MathMarkupError()
    return text
