[English](design.md) · [中文](design.zh.md)

# infra/print 设计

只写结论，给要改本组件设计的人看。怎么用：`BRICKKIT.md`；代码怎么组织：`AGENTS.md`。

## 边界

本组件负责渲染：模板 ID 加数据，产出 PDF 或 ZPL 指令流。它保存模板及其版本，并记录每次渲染。它是纯函数：同一模板版本加同样的数据，输出相同。

| 不在这里 | 归谁 | 为什么 |
|---|---|---|
| 要不要打印、什么时候打印（"金额大于 10 万的订单才打印"） | 业务组件 | 那是业务逻辑；本组件收到请求就渲染 |
| 数据从哪来、对不对 | 调用方，它把数据整个以 JSON 传进来 | 回查数据就得认识每个业务库的结构；本组件甚至不知道打的是不是一张真订单 |
| 谁能看到被打印的业务数据 | 调用方 | 模板是共享资产；调用方在调 `Render` 之前按自己的权限与数据范围判定 |
| 打印机、驱动、纸张 | 客户端 | 输出是字节流，送到打印机是客户端的事 |
| 保存渲染结果 | 存储或附件组件 | 输出直接返回，从不落盘 |

两条渲染路径只共用入口：

| | A4 单据 | 条码标签 |
|---|---|---|
| 模板 | HTML + CSS（分页媒体） | ZPL 指令文本（`^XA … ^XZ`） |
| 输出 | PDF 字节流 | 纯文本，直接送斑马打印机 |
| 引擎 | WeasyPrint | 没有：只做字符串替换 |

标签绝不经"HTML 渲染成图片"来做：那会让标签——最需要精确到点的东西——依赖整个 HTML 渲染栈。

## 拥有的数据

| 表 | 分区 | 说明 |
|---|---|---|
| `print_templates` | 不分区 | 每个模板的当前版本：`id`（文本，由上传的人定，例如 `sales.delivery_note`）、名称、`channel`（`PDF` 或 `ZPL`，有 CHECK）、内容、`version`、`enabled`。`version` 既是版本号也是乐观并发计数：每次编辑恰好加一 |
| `print_template_versions` | 不分区 | 写过的每一个版本，只插入：名称、通道、内容、`version`（同一模板内唯一）、`enabled`（写入这个版本时模板的启用状态）、`created_at`。引用 `print_templates` |
| `print_jobs` | 按 `created_at` 月分区 | 每次正式渲染一行：模板、`actor`、`source_component`、`SUCCESS` / `FAILED`、错误。只存元数据，从不存渲染出的字节（每次几百 KB）。写入即终态 |
| `event_outbox` | 按 `created_at` 周分区 | 推送用的 outbox。没有 `event_inbox`：什么都不消费 |

没有 `command_idempotency` 表。模板存在数据库里而不是文件里：上传、预览、回滚都是运行期行为，组件清单没有 volumes，外壳镜像里也没有本仓库的副本。

回滚是把旧版本复制成新的当前版本（版本号继续往前走）；任何版本都不删、不改写。回滚不改启用开关，所以新版本记下的是模板当时的状态。`enabled` 列出现之前写入的行已回填：当前版本取模板的状态，更早的版本按启用记。

## 契约面

gRPC `infra.print.v1.PrintService`，给别的组件用：

| rpc | 类型 | 说明 |
|---|---|---|
| `Render` | 无副作用的命令 | `template_id` + `data_json` + `source_component` → 字节流 + `content_type`。没有幂等键：重复调用就是再渲染一遍，调用方超时可以直接重试，不用先查状态 |
| `BatchGetTemplates` | 读 | 批量读；按请求顺序返回，查不到的 ID 省略 |
| `ListTemplates` | 读 | 按通道筛选；游标 = 模板 ID（`id > cursor`），从不用 offset |

`source_component` 是调用方的一面之词：组件间 gRPC 不带经过验证的调用方身份，所以它只为排障记下来，不做核实。

REST 在 `/infra/print` 下，每条路由都有权限键：`POST /render`（`infra.print.render`），`GET /templates`、`GET /templates/{id}`、`GET /templates/{id}/versions`（`infra.print.template.view`），`PUT /templates/{id}`、`POST /templates/{id}/rollback`、`POST /templates/{id}/preview`（`infra.print.template.edit`）。预览要编辑权限，因为它是编辑时的所见即所得核对；它用样例数据渲染，什么都不记。能改模板不等于能打单据：`render` 有自己的键。

`GET /templates/{id}/versions` 按新的在前列出版本，`{ versions: [{ version, name, enabled, created_at }], next_cursor }`，回滚时就能让人选，不用手填版本号。游标是上一页最后一个版本号（`version < cursor`）；每页 1–200，默认 50。模板不存在是 `404`，不是空列表；游标不是版本号是 `400`。

没有状态端点：这里没有长时间运行的写操作。

**模板**是完整的 Jinja2，送货单才能按行循环明细。语法不阉割成纯变量替换，而是：

- 渲染跑在 Jinja2 的 `SandboxedEnvironment` 里：模板表达式摸不到 Python 内部对象（`__class__`、`__globals__` …）。模板由有编辑权限的人上传，运行在服务进程里，进外壳后就是所有成员共用的进程。
- PDF 输出做 HTML 转义（`autoescape`），因为数据里有用户输入（客户名称、备注）；ZPL 不转义，转义会把指令改坏。
- WeasyPrint 只加载 `data:` URI。`file://` 能让模板把进程读得到的任何文件作为附件嵌进 PDF，包括带数据库口令的 `/proc/self/environ`；`http(s)://` 能打到内部服务。被拒的资源记日志后跳过；图片以 `data:` URI 内嵌。
- 模板里不写业务规则（税率、折扣），这条靠评审守，不靠语法限制。

引擎藏在 `render(template, data) -> bytes` 后面；`render/` 以外没有任何地方 import WeasyPrint。引擎确实会被换掉（两个知名开源 ERP 都在逐步离开 wkhtmltopdf），换的时候只动一个文件，不动契约。

gRPC 消息上限 4 MB。一张送货单 PDF 大约 100 KB；一次打几百张标签要拆成几次调用。

## 发布与消费的事件

发布，与 `print_jobs` 那一行在同一个事务里经 outbox 发出：

| Subject | 何时 | Payload |
|---|---|---|
| `infra.print.rendered.v1` | 每次正式渲染（`Render`、`POST /render`）之后，成功失败都发 | `job_id`、`template_id`、`actor`、`source_component`、`success`、`error` |

旁路事件，留给审计组件：打印不改变任何业务状态，没有下游在等它。预览不发。

消费：无。消费业务事件就意味着要理解业务语义，本组件不能这样做。

## 依赖

没有依赖，这是设计如此。

| 不是依赖 | 为什么 |
|---|---|
| 任何业务组件 | 数据随请求而来；回查数据会同时打破"没有业务逻辑"与"不认识业务库结构" |
| 存储或附件组件 | 输出直接返回，不保存 |
| `infra/iam-casdoor` | token 在本地用 `IAM_JWKS_URL` 的 JWKS 验：是配置，不是依赖边 |
| `infra/authz` | 权限 bundle 从 `AUTHZ_BUNDLE_URL` 轮询：是配置，不是依赖边 |
| NATS | 经 `NATS_URL` 访问的基础资源，不是组件 |

## 在同步调用图里的位置

只有入边的叶子；它不调用任何人，所以不可能闭合一个环。现在是 PC 前端经 REST 调它（"打印送货单"按钮渲染 `sales.delivery_note`）；要在服务端打印的业务组件经 gRPC 调 `Render` 并声明依赖。它不属于三大枢纽，也与"CRM 与 ERP 之间没有同步边"无关：它是基础设施。

因为它生来就是被依赖的，它的替代方案（见参考实现）是保留 `metadata.id` 与契约的分叉，而不是一组可互换的槽位组件。

## 分区与归档

| 数据 | 热数据 | 何时归档 | 归档到哪 |
|---|---|---|---|
| `print_templates`、`print_template_versions` | 永远 | 从不：每个版本都要能回滚 | — |
| `print_jobs` | 最近三个月 | 早于三个月的整月分区 | `infra_print_archive` |

`print_jobs` 是审计证据（"谁打过这张发票"），旧数据要归档，不删。组件自己提前三个月建 `print_jobs` 的月分区、提前四周建 `event_outbox` 的周分区（启动时检查一次，之后每 24 小时一次）；归档这一步本身还没有实现（见未决问题）。

## 数据范围

没有（`data_scopes: none`）。模板是共享资产：一家公司就一套送货单模板，能打开模板页面的人都看到全部模板。渲染结果里确实有业务数据，但谁能看这些数据，由调用方在调 `Render` 之前判定；本组件无从知道它打的是谁的订单。

## 参考实现

| 项目 | 版本 | 看的模块 | 借鉴了什么 | 许可证 | 用法 |
|---|---|---|---|---|---|
| ERPNext / Frappe | — | Print Format（Jinja HTML 模板）与 PDF 生成 | 单据格式用 HTML 模板；从 wkhtmltopdf 迁往 Chromium 这件事，证明引擎必须可替换 | GPL-3 | 借鉴思路 |
| Odoo | — | QWeb 报表 → wkhtmltopdf | 同样是 HTML 模板；在每个调用点绑死一个引擎的代价 | LGPL-3 | 借鉴思路 |
| OCA `report_py3o`、Tryton `relatorio` | — | ODT（LibreOffice）模板，只读文档 | 第二流派存在：业务人员不用开发者，自己在 LibreOffice 里改模板 | AGPL-3 / GPL-3 | 借鉴做法 |
| WeasyPrint | 63.1 | 分页媒体支持、字体与 CJK、`url_fetcher` 钩子 | 默认引擎；中文要 `fonts-noto-cjk` 并显式写 `Noto Sans CJK SC` | BSD-3 | 库 |
| Jinja2 | 3.1.6 | `jinja2.sandbox` | 用户上传模板的沙箱 | BSD-3 | 库 |
| 斑马 ZPL II | — | 指令参考 | 标签路径直接出 ZPL 文本 | 闭源规范 | 借鉴做法 |

**刻意没采用**：打印模块自己查业务表来填单据（ERP 里很常见）；在每个调用点绑死引擎；标签经 HTML 转图片。Java 报表引擎（JasperReports、BIRT）没有进一步考虑：要么换技术栈，要么起一个外壳托管不了的边车进程。

**真实的分歧**，两条都是分叉而不是槽位（调用方按 ID 依赖本组件）：模板形态（这里是 HTML；没有 IT 团队的客户适合 ODT）与引擎（这里是 WeasyPrint；版式保真比镜像体积更要紧时用 Chromium）。两者都是替换 `render()` 背后实现、保留 `Render` 契约的分叉。

## 未决问题

| 问题 | 目前的答案 |
|---|---|
| `print_jobs` 三个月后归档 | 有此打算（见分区与归档），还没实现：月分区一直留在热 schema 里，直到写出归档循环 |
| 一次渲染几百张标签撞 gRPC 4 MB 上限 | 调用方拆批。以后：渲染结果存进存储组件，只返回下载地址 |
| 同一模板的两个并发 `PUT` | 两边算出同一个下一版本号；后一个撞 (模板, 版本) 唯一索引，回服务器错误。编辑模板是少见的管理操作；在这个冲突上重试就能消除 |
| 模板里的 logo 等图片 | 只支持 `data:` URI。模板要共用图片时，走存储组件加取数器里的白名单，绝不放开 `http(s)` 取数 |
| ZPL 标签上的中文 | 要 `^CI28` 与打印机上的中文字体；由模板作者负责，这里不检查 |
