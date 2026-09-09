"""HTML→PDF 渲染——WeasyPrint 是阶段三的默认实现（设计计划 §3.3）。

⚠️ 业务代码（service 层）不许直接 import weasyprint——只通过
``app.render.get_renderer`` 拿 ``Renderer`` 接口，换引擎（比如以后要支持
Chromium）只改这一个文件，不改调用点。

模板用 Jinja2 做变量替换——⚠️ 设计计划 §9 待决问题 3 的既定原则是"只做
纯变量替换 + 格式化，不做计算"，Jinja2 本身的表达式语法比这更宽松（能写
``{{ a + b }}`` 这类计算）。这里刻意不去做技术上的沙箱阉割：真实的送货单/
发票模板需要按行遍历商品明细（``{% for item in items %}``），一个只支持
``{{ field }}`` 单变量替换、没有循环能力的引擎做不出这类文档，锁死循环
语法的技术方案代价太高、收益有限。**边界靠代码评审守，不靠语法阉割**——
模板里出现金额计算这类业务规则是 review 时该拦的事，不是这个类要负责
阻止的事（同 §6.11"严禁业务逻辑"的精神：本组件本身、包括这层渲染引擎，
不会替调用方算任何东西，模板里写不写只取决于写模板的人有没有守规矩）。
"""

from __future__ import annotations

import jinja2
from weasyprint import HTML

# autoescape=True：数据来自业务组件，可能含用户输入（客户名称、备注等），
# 送货单渲染成 HTML 前必须转义，否则是一个真实的 HTML 注入面
# （渲染出的 PDF 内容被污染，不是常见的"网页 XSS"，但同一类问题）。
_env = jinja2.Environment(autoescape=True)


class WeasyPrintRenderer:
    content_type = "application/pdf"

    def render(self, template_content: str, data: dict) -> bytes:
        html = _env.from_string(template_content).render(**data)
        return HTML(string=html).write_pdf()
