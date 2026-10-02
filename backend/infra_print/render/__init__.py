"""渲染的唯一分流点：两条完全不同的渲染路径（HTML → PDF、模板 → ZPL）共用一个
入口，模板的 channel 决定走哪条。service 层只调 ``get_renderer``，不直接 import 具体引擎。
"""

from __future__ import annotations

from infra_print.render.base import Renderer
from infra_print.render.pdf import WeasyPrintRenderer
from infra_print.render.zpl import ZplRenderer

_RENDERERS: dict[str, Renderer] = {
    "PDF": WeasyPrintRenderer(),
    "ZPL": ZplRenderer(),
}


def get_renderer(channel: str) -> Renderer:
    renderer = _RENDERERS.get(channel)
    if renderer is None:
        msg = f"未知渲染通道：{channel!r}"
        raise ValueError(msg)
    return renderer


__all__ = ["Renderer", "get_renderer"]
