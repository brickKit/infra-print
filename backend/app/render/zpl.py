"""ZPL 指令渲染——纯字符串模板替换，不需要任何渲染引擎（设计计划 §3.2）。

⚠️ 不许用"HTML → 图片 → ZPL"把两条路合成一条：那会让标签打印依赖整个
HTML 渲染栈，而条码标签恰恰是最不能出错、最需要精确到点的东西（扫不出来
的条码等于没打）。ZPL 就是为此设计的指令集，直接生成它是正解。
"""

from __future__ import annotations

import jinja2

# autoescape=False：ZPL 是纯文本指令（`^XA...^XZ`），不是 HTML，没有转义
# 的概念——对它做 HTML 转义反而会把指令文本改坏（比如把 `&` 转成 `&amp;`）。
_env = jinja2.Environment(autoescape=False)  # noqa: S701 - ZPL 不是 HTML，见上


class ZplRenderer:
    content_type = "text/plain"

    def render(self, template_content: str, data: dict) -> bytes:
        text = _env.from_string(template_content).render(**data)
        return text.encode("utf-8")
