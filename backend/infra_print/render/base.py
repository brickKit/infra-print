"""渲染引擎的内部可替换接口——设计计划 §3.3：换引擎不该是"改 40 处调用"，
业务代码只认这一个接口 render(template, data) -> bytes。WeasyPrint 是
阶段三的默认实现，Chromium 是版式要求高时的 Fork 点，两者都得实现这个
接口，业务代码（service 层）永远不直接 import 具体引擎。
"""

from __future__ import annotations

from typing import Protocol


class Renderer(Protocol):
    content_type: str

    def render(self, template_content: str, data: dict) -> bytes: ...
