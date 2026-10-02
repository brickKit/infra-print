"""渲染引擎的内部接口 ``render(template, data) -> bytes``。业务代码只认它：WeasyPrint 是
PDF 的默认实现，换成 Chromium 之类的引擎只换实现，不改调用点，也不改契约。
"""

from __future__ import annotations

from typing import Protocol


class Renderer(Protocol):
    content_type: str

    def render(self, template_content: str, data: dict) -> bytes: ...
