"""HTML → PDF：Jinja2 填数据，WeasyPrint 出 PDF。service 层只经
``infra_print.render.get_renderer`` 拿 ``Renderer``，不直接 import weasyprint：
换引擎（例如 Chromium）只改这一个文件。

模板语言是完整的 Jinja2（送货单要按行循环明细），但跑在沙箱里：模板由有
``infra.print.template.edit`` 的人上传，表达式摸不到 Python 内部对象。模板里不写
计算型业务规则（税率、折扣），这条靠评审守，语法上不拦。

渲染时不替模板取任何外部资源：只认 ``data:`` URI。``file://`` 会把服务进程里的文件
（包括 ``/proc/self/environ`` 里的数据库口令）作为附件嵌进 PDF，``http(s)://`` 会从
服务所在的网络里打内部地址。被拒的资源由 WeasyPrint 记一条 ERROR 日志后跳过。
"""

from __future__ import annotations

from jinja2.sandbox import SandboxedEnvironment
from weasyprint import HTML, default_url_fetcher

# autoescape=True：数据来自业务组件，可能含用户输入（客户名称、备注），
# 渲染成 HTML 前必须转义，否则数据能改写单据版式。
_env = SandboxedEnvironment(autoescape=True)


def _data_uri_only(url: str, timeout: int = 10, ssl_context=None) -> dict:
    if url.startswith("data:"):
        return default_url_fetcher(url, timeout=timeout, ssl_context=ssl_context)
    msg = f"模板只能内嵌 data: URI 资源，不取外部地址：{url}"
    raise ValueError(msg)


class WeasyPrintRenderer:
    content_type = "application/pdf"

    def render(self, template_content: str, data: dict) -> bytes:
        html = _env.from_string(template_content).render(**data)
        return HTML(string=html, url_fetcher=_data_uri_only).write_pdf()
