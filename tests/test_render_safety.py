"""渲染引擎的安全边界（L1，不连库）：模板内容由有 infra.print.template.edit 的人上传，
渲染发生在本组件进程里（进外壳后是所有成员共用的进程）。

- 模板表达式不能摸到 Python 内部对象：否则编辑模板的人能在服务进程里执行任意代码。
- PDF 渲染不能替模板去取外部资源：file:// 能把进程里的文件（含 /proc/self/environ 里的
  数据库口令）作为附件嵌进 PDF，http(s):// 能从服务所在网络里打内部地址。模板要的
  图片一律写成 data: URI。
"""

from __future__ import annotations

import http.server
import re
import threading
import zlib
from pathlib import Path

import pytest

from infra_print.render import get_renderer
from infra_print.render.pdf import WeasyPrintRenderer
from infra_print.render.zpl import ZplRenderer

# 能拿到 os 模块的经典写法：cycler 是 Jinja2 默认放进全局的一个类
_ESCAPE = "{{ cycler.__init__.__globals__.os.getcwd() }}"
_MRO = "{{ ''.__class__.__mro__[1].__subclasses__() }}"


@pytest.mark.parametrize("renderer", [WeasyPrintRenderer(), ZplRenderer()], ids=["PDF", "ZPL"])
@pytest.mark.parametrize("template", [_ESCAPE, _MRO], ids=["globals", "mro"])
def test_模板表达式摸不到Python内部对象(renderer, template) -> None:
    with pytest.raises(Exception) as exc:  # noqa: PT011 - 只要渲染失败，类型由 Jinja2 决定
        renderer.render(template, {})
    assert "unsafe" in str(exc.value).lower() or "security" in type(exc.value).__name__.lower()


def test_沙箱里循环与过滤器照常可用() -> None:
    out = ZplRenderer().render(
        "{% for i in items %}{{ i.name | upper }}:{{ '%.2f' | format(i.qty) }};{% endfor %}{{ items | length }}",
        {"items": [{"name": "a", "qty": 1}, {"name": "b", "qty": 2.5}]},
    )
    assert out == b"A:1.00;B:2.50;2"


class _CountingHandler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定名
        _CountingHandler.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"internal-secret")

    def log_message(self, *args) -> None:  # 不往测试输出里打访问日志
        pass


def _pdf_streams_text(pdf: bytes) -> bytes:
    """把 PDF 里每个 stream 尽量解压后拼起来，用来查附件内容有没有被嵌进去。"""
    out = [pdf]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", pdf, re.S):
        try:
            out.append(zlib.decompress(m.group(1)))
        except zlib.error:
            out.append(m.group(1))
    return b"\n".join(out)


def test_PDF渲染不替模板取file与http资源(tmp_path: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("FILE-MARKER-4f1c", encoding="utf-8")
    server = http.server.HTTPServer(("127.0.0.1", 0), _CountingHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _CountingHandler.hits.clear()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    template = f"""<html><head>
<link rel="attachment" href="file://{secret}">
<link rel="stylesheet" href="{base}/style.css">
</head><body>
<img src="{base}/logo.png">
<a rel="attachment" href="{base}/doc.txt">doc</a>
<p>正文</p>
</body></html>"""
    try:
        pdf = WeasyPrintRenderer().render(template, {})
    finally:
        server.shutdown()
    assert pdf[:4] == b"%PDF"
    assert _CountingHandler.hits == [], f"渲染时打了外部地址：{_CountingHandler.hits}"
    assert b"FILE-MARKER-4f1c" not in _pdf_streams_text(pdf), "本地文件被作为附件嵌进了 PDF"


def test_PDF里data_URI图片照常可用() -> None:
    # 1x1 透明 PNG
    png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
    )
    pdf = get_renderer("PDF").render(f'<img src="data:image/png;base64,{png}"><p>{{{{ x }}}}</p>', {"x": "ok"})
    assert pdf[:4] == b"%PDF"
    assert b"/Image" in pdf or b"/XObject" in pdf
