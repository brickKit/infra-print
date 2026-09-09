# infra-print · 打印渲染中心

全系统第一个 Python 组件。纯函数渲染中心：`template_id` + 数据（JSON）→ PDF 字节流或 ZPL 指令流。**严禁包含任何业务逻辑**——收到请求就渲染，不判断"要不要打印"、不回查任何业务库（设计书 §6.11）。

## 它能做什么

- `infra.print.v1.PrintService`（gRPC，组件间协议）：`Render`（**全系统唯一不带 `idempotency_key` 的写命令 rpc**——本组件是纯函数，重复调用没有副作用）、`BatchGetTemplates`（防 N+1）、`ListTemplates`
- REST（人类操作，`/infra/print/**`）：
  - `POST /render`：前端直接要预览/打印时用
  - `GET /templates` / `GET /templates/{id}`：模板清单与详情
  - `PUT /templates/{id}`：上传新版本
  - `POST /templates/{id}/rollback`：回滚到指定历史版本——把历史版本复制成新的当前版本，不是删掉新版本
  - `POST /templates/{id}/preview`：用样例数据预览，**不写 `print_jobs`**（不算一次正式渲染）
- 两条完全不同的渲染路径，共用 `Render` 一个入口，按 `template.channel` 内部分流：**PDF**（HTML+CSS 模板 → WeasyPrint）、**ZPL**（斑马打印机指令文本模板 → 纯字符串替换，不经过任何渲染引擎）
- 后台循环：Outbox 推送（`infra.print.rendered.v1` 旁路审计事件）、`event_outbox` 周分区维护、`print_jobs` 月分区维护

⚠️ **没有 `command_idempotency` 表**——同样的 `template_id` + 同样的数据 = 同样的输出，重复调用直接重算一遍，不需要先查状态（设计计划 §1.1）。这一条与全系统写命令都要幂等键的惯例相反，别照别的组件的模板给这里加一张空表。

## 需要哪些基础资源

| 资源 | 形态 | 为什么需要 | 怎么起 |
|---|---|---|---|
| PostgreSQL 16 | **A**（`kind: database`） | `print_templates`/`print_template_versions`/`print_jobs`（月分区）独占 schema `infra_print` | 装配仓库根目录 `make up` |
| NATS 2.10 | **A**（`kind: mq`） | 只用来发 `infra.print.rendered.v1` 旁路审计事件——本组件不消费任何事件 | 同上 |

⚠️ **零强依赖零弱依赖，零出边**——本组件是纯函数，数据由调用方整个传进来，不查任何业务库。`erp-sales` 等业务组件对 `Render` 建了强依赖边（入边），本组件反过来对谁都不建边。

## 怎么起来

```bash
# 装配仓库根目录先起基础资源
make up
make db-init   # 建 infra_print / infra_print_rw

cd components/infra/print
uv venv .venv --python 3.12
uv pip install -e . --python .venv/bin/python3

# 迁移要用有建表权限的账号（不是 infra_print_rw，那个角色 Cannot login）
DATABASE_HOST=localhost DATABASE_PORT=5432 DATABASE_USER=postgres \
  DATABASE_PASSWORD=<.env 里的 POSTGRES_PASSWORD> DATABASE_NAME=brickkit_db PG_SCHEMA=infra_print \
  .venv/bin/python3 -m app.migrate apply

COMPONENT_ID=infra/print COMPONENT_VERSION=1.0.0 \
  DATABASE_HOST=localhost DATABASE_PORT=5432 DATABASE_USER=postgres \
  DATABASE_PASSWORD=<同上> DATABASE_NAME=brickkit_db PG_SCHEMA=infra_print \
  MQ_HOST=localhost MQ_PORT=4222 OTEL_BASE_URL="" \
  .venv/bin/python3 -m app.main
```

或者用平台：`brickkit up`。

⚠️ **数据库名是 `brickkit_db`，不是 `postgres`**——本仓库真机验证时踩过一次这个坑：`docker exec be-postgres psql -U postgres` 默认连的是 `postgres` 库，`infra_print` schema 建在 `brickkit_db` 里，连错库会看到"schema 不存在"这种误导性报错。

## 怎么用

```bash
# 组件间协议：渲染一张标签（不需要 Authorization——gRPC 面不验 JWT，
# 同全项目"组件间 gRPC 目前不转发/验证身份"的既有约定）
grpcurl -plaintext -d '{"template_id":"delivery-note-a4","data_json":"{\"customer\":\"某某公司\"}","source_component":"erp/sales"}' \
  localhost:9400 infra.print.v1.PrintService/Render

# 人类操作：REST 面需要 Authorization
curl -X POST -H 'Authorization: Bearer <应用 token>' -H 'Content-Type: application/json' \
  -d '{"template_id":"delivery-note-a4","data":{"customer":"某某公司"}}' \
  http://localhost:8400/infra/print/render -o out.pdf
```

真机验证过完整链路：`grpcurl`/curl 打 `Render` → 真实 WeasyPrint 渲染出带中文的 A4 PDF → `pdftotext`/`pdftoppm` 人工核对内容与模板对得上，同时 `print_jobs` 落一条 `SUCCESS` 记录、`infra.print.rendered.v1` 真实发进 NATS。

## 配置项

| 配置键 | 默认值 | 说明 |
|---|---|---|
| `pgSchema` | `infra_print` | 本组件的 PG schema |
| `otelBaseUrl` | `""` | 空 = Blackhole Exporter，零成本 |
| `iamJwksUrl` | `""` | JWT 本地验签的公钥来源，指向 `infra-iam-casdoor` |
| `authzBundleUrl` | `""` | 权限判定的 bundle 轮询地址，指向 `infra-authz` |

## 参考实现

| 项目 | 看的模块 | 借鉴了什么 | 许可证 | 用法 |
|---|---|---|---|---|
| ERPNext / Frappe | Print Format（Jinja HTML 模板）+ PDF 生成后端 | 确认走 HTML 模板是主流路线；它正在把 `wkhtmltopdf` 换成 Chromium 这件事，直接推出"渲染引擎必须藏在内部接口后面"的结论 | GPL-3 | 借鉴逻辑 |
| Odoo | QWeb 报表 → wkhtmltopdf | 同样是 HTML 模板路线，佐证选型 | LGPL-3 | 借鉴逻辑 |
| WeasyPrint | Paged Media 支持范围、字体处理 | 选它做默认引擎；真机实测过中文字体在容器里怎么装（见「边界与禁令」） | BSD-3 | 借鉴逻辑 |
| 斑马 ZPL II | 指令集手册 | ZPL 是文本指令，不经过任何渲染引擎，直出即可 | 闭源规范 | 借鉴实际应用 |

完整调研过程见 [`docs/design/infra-print.md`](../../../docs/design/infra-print.md)。

## 边界与禁令

- **严禁任何业务逻辑**——不判断"要不要打印"、"什么时候打印"，收到请求就渲染。数据由调用方整个传入，本组件不回查任何业务库，甚至不知道 `data` 是不是一张真订单
- **没有 `Render` 的幂等键**——全系统唯一的例外，理由见上文"它能做什么"末尾
- **模板存数据库不存文件**——平台的 Manifest 没有 volumes 字段，文件挂不进容器
- **渲染引擎藏在内部接口后面**（`app/render/base.py` 的 `Renderer` Protocol）——业务代码不许直接 `import weasyprint`，换引擎（如未来的 Chromium Fork）不该是"改 40 处调用"
- **⚠️ WeasyPrint 的中文字体是真实踩过的坑**：只装 `libpango`/`libcairo`/`libgdk-pixbuf` 不装字体时，中文 PDF 只嵌入 `DejaVu-Serif`，`pdftotext` 提取出来是乱码且重复（不是"变方框"那么简单）。必须再装 `fonts-noto-cjk` + `fontconfig` 并跑 `fc-cache -f`（见 `Dockerfile`）。fontconfig 在没有显式 `lang` hint 时默认选 Noto CJK 的 **JP** 变体而不是 **SC**（简体中文），模板要显式写 `font-family: "Noto Sans CJK SC"`，不能依赖字体回退
- **`print_jobs` 只存元数据不存渲染结果字节**——那是几百 KB × 每次打印，只有审计与排障价值（谁、何时、渲染了哪个模板、成功与否）
- **`data_scopes: none`**——模板是全局资产，不做行级过滤；渲染出来的内容里有业务数据，但访问控制靠调用方自己判权限后再来调 `Render`
