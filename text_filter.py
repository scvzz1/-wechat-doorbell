"""Downgrade Markdown to WeChat-friendly plain text.

WeChat does not render Markdown: **bold**, ## headers, `code` and
[text](url) would arrive as literal symbols. This conservative filter
strips the syntax while keeping the content and line structure, so an
agent can write naturally and still land readable in chat.

Conservative on purpose: it only rewrites constructs that are almost
certainly Markdown (fences, headers, bold, links, table rows), and
leaves ordinary chat text — including stray asterisks — untouched.
"""
import re

_HR = re.compile(r"\s*(-{3,}|\*{3,}|_{3,})\s*")
_TABLE_SEP = re.compile(r"\s*\|?[\s:|-]+\|[\s:|-]*")


def filter_markdown(text: str) -> str:
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue  # drop the fence line itself, keep the code content
        if in_fence:
            out.append(line)
            continue
        s = line
        s = re.sub(r"^\s{0,3}#{1,6}\s+", "", s)        # ## headers
        s = re.sub(r"^\s{0,3}>\s?", "", s)             # > quotes
        if _HR.fullmatch(s):
            continue                                    # --- rules
        if _TABLE_SEP.fullmatch(s) and "-" in s:
            continue                                    # |---|---| rows
        if s.strip().startswith("|") and s.strip().endswith("|"):
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            s = " ｜ ".join(cells)                      # table row -> plain line
        s = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", s)           # images -> alt text
        s = re.sub(
            r"\[([^\]]+)\]\(([^)]+)\)",
            lambda m: m.group(1)
            if m.group(1) == m.group(2)
            else f"{m.group(1)}（{m.group(2)}）",
            s,
        )                                               # links -> text（url）
        s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)       # **bold**
        s = re.sub(r"__([^_]+)__", r"\1", s)           # __bold__
        s = re.sub(r"(?<!\w)\*([^*\n]+)\*(?!\w)", r"\1", s)  # *italic*
        s = re.sub(r"`([^`\n]+)`", r"\1", s)           # `code`
        out.append(s)
    return "\n".join(out).strip("\n")
