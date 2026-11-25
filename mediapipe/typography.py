# -*- coding: utf-8 -*-
"""中文排版 —— 手机上读长文的三个硬规则

不是审美问题，是完读率问题：
  1. 数字要被看见：金额/百分比/倍数自动加粗
  2. 结论要跳出正文：`为什么：/影响：/结论：` 转引用块
  3. 长段在手机上必被跳过：超过 N 字按句切分，成段更短
"""
import re

NUM_RE = re.compile(
    r"(?<![\d.])(?:"
    r"\$?\d+(?:\.\d+)?\s*(?:美元|元|倍|%|％|亿|万|千|天|小时|分钟|秒|token|GB|MB)"
    r"|\d+(?:\.\d+)?%"
    r")(?![\d])"
)
LEAD_RE = re.compile(r"^\s*(为什么|影响|结论|怎么理解|一句话|要点)[：:]\s*(.+)$", re.M)
SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])")


def bold_numbers(text: str) -> str:
    """给关键数字加粗（已在 ** ** 里的跳过）"""
    def repl(m):
        s = m.group(0)
        start = m.start()
        if text[max(0, start - 2):start] == "**":
            return s
        return f"**{s}**"
    return NUM_RE.sub(repl, text)


def lead_to_quote(text: str) -> str:
    """把 `结论：xxx` 这类引导句转成引用块"""
    return LEAD_RE.sub(lambda m: f"> **{m.group(1)}**：{m.group(2)}", text)


def split_long_paragraphs(text: str, max_len=110) -> str:
    """长段按句切分：一段不超过 max_len 字（代码块/表格/标题内不动）"""
    blocks = text.split("\n")
    out = []
    in_code = False
    for b in blocks:
        s = b.strip()
        if s.startswith("```"):
            in_code = not in_code
            out.append(b)
            continue
        if (in_code or len(s) <= max_len or not s
                or s.startswith(("#", ">", "|", "-", "*", "1."))):
            out.append(b)
            continue
        sents = [x for x in SENT_SPLIT.split(s) if x.strip()]
        buf, lines = "", []
        for sen in sents:
            if len(buf) + len(sen) > max_len and buf:
                lines.append(buf)
                buf = sen
            else:
                buf += sen
        if buf:
            lines.append(buf)
        out.extend(line.strip() for line in lines)
        out.append("")
    return "\n".join(out).rstrip()


def normalize_punct(text: str) -> str:
    """半角标点转全角（中文语境），并把连续空行压成一个"""
    text = re.sub(r"(?<=[\u4e00-\u9fa5]),(?![0-9])", "，", text)
    text = re.sub(r"(?<=[\u4e00-\u9fa5])\.(?![0-9])", "。", text)
    text = re.sub(r",\s*(?=[\u4e00-\u9fa5])", "，", text)
    return re.sub(r"\n{3,}", "\n\n", text)


def render(text: str) -> str:
    """完整流水线；围栏代码块原样保护，不对代码内容做中文排版。"""
    protected = []
    def mask_code(match):
        protected.append(match.group(0))
        return f"\x00CODEBLOCK{len(protected) - 1}\x00"

    # Support both backtick and tilde fenced blocks, including language labels.
    pattern = re.compile(r"(?ms)^ {0,3}(`{3,}|~{3,})[^\n]*\n.*?^ {0,3}\1[^\n]*(?:\n|$)")
    masked = pattern.sub(mask_code, text)
    rendered = split_long_paragraphs(lead_to_quote(bold_numbers(normalize_punct(masked))))
    for index, block in enumerate(protected):
        rendered = rendered.replace(f"\x00CODEBLOCK{index}\x00", block)
    return rendered
