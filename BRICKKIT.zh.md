# infra/print

## 组件定位

纯函数的渲染服务：模板 ID 加上 JSON 数据，产出 PDF（送货单这类 A4 单据）或 ZPL 指令流（斑马条码标签）。同一个模板版本加同样的数据，输出永远相同；组件里没有业务逻辑，也从不回查任何东西。

**负责**

- 打印模板及其版本：上传模板（`PUT`）产生一个新版本，回滚是把旧版本复制成新版本，任何版本都不删除。每个模板有一个通道——`PDF`（HTML + CSS，由 WeasyPrint 渲染）或 `ZPL`（纯文本）——和一个启用开关。
- 渲染：Jinja2 填模板（支持循环与过滤器，运行在沙箱里），再按通道选引擎。PDF 渲染不加载任何外部资源：图片与样式必须以 `data:` URI 内嵌。
- 每次正式渲染在 `print_jobs` 里记一条（谁、哪个模板、哪个组件调的、成功还是什么错误），并发旁路事件 `infra.print.rendered.v1` 供审计。

**不负责**

- 要不要打印、什么时候打印、单据上写什么：由业务组件决定（例如送货单归 `erp/sales`），它把全部数据放进请求里。
- 谁能看到被打印的业务数据：调用方在调 `Render` 之前按自己的权限与数据范围判定；这里的模板对所有人共享。
- 打印机、驱动、纸张与打印队列：客户端拿到字节流后自己送打印机。
- 保存渲染结果：输出直接返回、从不落盘；长期存储归存储或附件组件。

## 部署前准备

- **PostgreSQL**：schema `infra_print`，按你的 schema 约定还可以有 `infra_print_archive`（目前没有任何东西写它）。登录角色 `infra_print_rw`，在 `infra_print` 上有 `USAGE` 与 `CREATE`，以及它的密码。迁移（`python -m infra_print.migrate apply`，用本组件自己的镜像跑）以这个角色登录，在 schema 里建表和 yoyo 自己的状态表（`_yoyo_log`、`_yoyo_migration`、`_yoyo_version`、`yoyo_lock`），所以表的属主是这个角色；这一点要紧，因为运行中的组件要自己给 `print_jobs` 建月分区、给 `event_outbox` 建周分区。BrickKit 不创建这些；在 BrickEnterprise 装配项目里，`make dev-env` 把密码写进 `.env`，`make db-init` 建 schema、角色与授权。
- **NATS** 在 `NATS_URL` 可达：渲染事件经 outbox 表与后台推送发出；NATS 不可达时组件照常启动，事件在 outbox 里等着。
- **授权**（`infra/authz`）与**身份**（`infra/iam-casdoor`，或任何提供 JWKS 的 IAM）在 `AUTHZ_BUNDLE_URL`、`IAM_JWKS_URL` 可达，REST 路由才会返回错误以外的东西。它们是配置不是依赖：没有它们组件照样启动。gRPC 服务不验 token。
- **字体与系统库**已在镜像里：WeasyPrint 要的 Pango、Cairo、HarfBuzz、GDK-Pixbuf，以及 `fonts-noto-cjk` 并建好 fontconfig 缓存。托管本组件的 Python 外壳必须装同一份；缺了中文字体，镜像照样能构建、`/healthz` 照样是绿的，但 PDF 里的中文是乱码。
- **以本机进程运行**（部署文件里 `mode: local`，或 `brickkit up --focus infra/print`）：`local.runCommand` 是 `.venv/bin/python -m infra_print.main`，相对组件目录，所以先在组件目录 `make venv`；没有 `.venv` 进程起不来。这时本机要有镜像里原本提供的东西：WeasyPrint 的系统库与 Noto CJK 字体。
- **模板**：组件不自带模板。用 `PUT /infra/print/templates/{id}` 上传；中文文字写 `font-family: "Noto Sans CJK SC"`，图片以 `data:` URI 内嵌。在 BrickEnterprise 项目里，组件跑起来之后在组件目录 `make seed`，灌一份默认送货单 `sales.delivery_note`（两个版本）和一份发货标签 `sales.shipping_label`。

## 依赖说明

没有依赖。调用方把单据需要的数据全部传进来，所以本组件不调用任何组件、不消费任何事件。业务组件经 gRPC 调它（`Render`），浏览器经 REST 调它（`POST /infra/print/render`）。

授权 bundle 与 JWKS 从配置里的 URL 拉取，不是依赖边。没有它们时每个受保护路由都失败关闭：`IAM_JWKS_URL` 为空 → 不看 token 就 `403`；没带 token，或 token 验不过（JWKS 不可达时也是这样）→ `401`；bundle 从没拉到过 → `503`；合法用户但没有权限键 → `403`。`/healthz` 始终 `200`：它只报告本进程活着。

## 配置指南

| 变量 | 含义 |
|---|---|
| `PG_HOST` | PostgreSQL 主机。通常用项目共享值（`$var:PG_HOST`）。 |
| `PG_PORT` | PostgreSQL 端口；默认 `5432`。 |
| `PG_DATABASE` | `infra_print` schema 所在的数据库（`$var:PG_DATABASE`）。 |
| `PG_USER` | 登录角色，写字面量 `infra_print_rw`。在外壳里由外壳用自己的角色登录，每个事务 `SET LOCAL ROLE` 切到这个角色，所以角色名必须是 `<PG_SCHEMA>_rw`。 |
| `PG_PASSWORD` | `PG_USER` 的密码。密钥：写 `${INFRA_PRINT_DB_PASSWORD}`（或你的密钥库引用），不写明文。 |
| `PG_SCHEMA` | 全部表、outbox 与 yoyo 状态表所在的 schema；默认 `infra_print`。仍然写上字面量，让每个组件的 schema 在一处可见。 |
| `NATS_URL` | outbox 推送的 NATS 服务器（`$var:NATS_URL`）。 |
| `OTEL_BASE_URL` | OpenTelemetry collector 基地址；为空（默认）时什么都不导出。 |
| `AUTHZ_BUNDLE_URL` | 权限判定轮询的授权 bundle 地址，例如 `http://infra-authz-2-0-0:8223/authz/bundle`。必填：没有它每个受保护路由都回 `503`。要与项目里运行的 authz 版本保持一致。 |
| `IAM_JWKS_URL` | 本地验用户 token 用的 JWKS 地址，例如 `http://infra-iam-casdoor-2-0-0:8200/.well-known/jwks.json`。必填：没有它每个受保护路由都回 `403`。 |

## 契约索引

- `contracts/infra/print/v1/print.proto`——gRPC `infra.print.v1.PrintService`，给别的组件用：`Render`（`template_id`、`data_json`、`source_component` → `content` 字节与 `content_type`，`application/pdf` 或 `text/plain`；没有幂等键，重复调用就是再渲染一遍；模板不存在 → `NOT_FOUND`，已停用 → `FAILED_PRECONDITION`，JSON 不合法或模板渲染失败 → `INVALID_ARGUMENT`），`BatchGetTemplates`（按请求顺序返回，查不到的 ID 省略），`ListTemplates`（按通道筛选，按 ID 游标分页，每页最多 200、默认 50）。生成的 Python 包是 `gen/` 下的 `infra.print.v1`。gRPC 消息上限 4 MB：大批量标签分几次调用。
- `contracts/print.openapi.yaml`——REST 在 `/infra/print` 下，每条路由都有权限键：`POST /render`（`infra.print.render`；`404` 模板不存在，`409` 已停用，`422` 渲染失败），`GET /templates` 与 `GET /templates/{id}`（`infra.print.template.view`），`GET /templates/{id}/versions`（`infra.print.template.view`；新的在前，`{ versions: [{ version, name, enabled, created_at }], next_cursor }`，参数 `cursor` / `page_size`；`404` 模板不存在，`400` 游标不对），`PUT /templates/{id}`（`infra.print.template.edit`；产生下一个版本；`channel` 只能是 `PDF` 或 `ZPL`，否则 `422`），`POST /templates/{id}/rollback`（`infra.print.template.edit`；把指定版本复制成新版本），`POST /templates/{id}/preview`（`infra.print.template.edit`，因为预览是编辑的一部分；渲染但不记 job、不发事件）。
- `contracts/events/print.events.json`——经 outbox 发布：`infra.print.rendered.v1`，每次正式渲染之后发，成功失败都发（`job_id`、`template_id`、`actor`——REST 上是用户的 `sub`，gRPC 上为空——、调用方自报的 `source_component`、`success`、`error`）。预览不发。不消费任何事件。
- `assembly.yaml`——本项目的元数据：上面三个权限键、菜单项、网关路由 `/infra/print/**`、schema 与角色，以及 `data_scopes: none`。

## 外壳声明

不是外壳。可以由 Python 外壳托管（在 BrickEnterprise 项目里是 `be/py-render`），也可以独立运行，两种形态是同一份代码。外壳从 `infra_print.module` 导入 `create_module`；本组件的顶层包是 `infra_print` 和命名空间包 `infra`（契约代码），不会被别的 Python 成员覆盖。
