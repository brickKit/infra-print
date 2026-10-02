"""ZPL 指令渲染：纯字符串模板替换，不经过任何渲染引擎。

不走"HTML → 图片 → ZPL"：那会让标签打印依赖整个 HTML 渲染栈，而条码标签最需要
精确到点（扫不出来的条码等于没打）。ZPL 本来就是为此设计的指令集。
"""

from __future__ import annotations

from jinja2.sandbox import SandboxedEnvironment

# 沙箱的理由同 pdf.py。autoescape=False：ZPL 是纯文本指令（^XA…^XZ），
# 做 HTML 转义反而会把指令改坏（& 变成 &amp;）。
_env = SandboxedEnvironment(autoescape=False)  # noqa: S701 - ZPL 不是 HTML，见上


class ZplRenderer:
    content_type = "text/plain"

    def render(self, template_content: str, data: dict) -> bytes:
        text = _env.from_string(template_content).render(**data)
        return text.encode("utf-8")
