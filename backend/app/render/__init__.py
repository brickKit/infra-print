"""Render(template, data) -> bytes 的唯一分流点——设计计划 §3.2：两条
完全不同的渲染路径共用一个入口，channel 字段决定走哪条。service 层只
调 ``get_renderer``，不直接 import 具体引擎（设计计划 §3.3）。
"""

from __future__ import annotations

from app.render.base import Renderer
from app.render.pdf import WeasyPrintRenderer
from app.render.zpl import ZplRenderer

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
