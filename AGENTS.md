# infra-print · AI 助手导读

## 身份证

| 项 | 值 |
|---|---|
| 组件 ID | `infra/print` |
| 仓库名 | `infra-print` |
| 端口 | HTTP `8400` / gRPC `9400`（`registry/ports.tsv`，装配仓库根目录那份） |
| schema / role | `infra_print` / `infra_print_rw`（归档 schema `infra_print_archive`，`print_jobs` 归档窗口 3 个月） |
| 语言 / 框架 | **Python**（全系统第一个）：FastAPI + uvicorn + `grpc.aio` + `asyncpg` + `yoyo-migrations` |
| 合并部署时进 | 外壳五 `py-render`（阶段四，本组件是里面唯一的模块） |
| 装配角色 | `default` |
| 设计真相源 | 装配仓库 `docs/design/infra-print.md`——本文件与它冲突时，以那份为准，回来改这里 |

## 边界

**归我：** 模板的存储与版本管理（上传、预览、回滚）、渲染（`template_id` + 数据 → PDF 字节流或 ZPL 指令流）。设计书 §6.11 的铁律：

| 什么 | 归谁 | 为什么 |
|---|---|---|
| "要不要打印"、"什么时候打印" | 业务组件 | §6.11 明令禁止任何业务逻辑。收到请求就渲染 |
| 数据从哪来、数据对不对 | 业务组件 | 调用方把渲染所需的 JSON 整个传给我，我不查任何业务库，甚至不知道它是不是一张真订单 |
| 打印机、驱动、纸张 | 客户端 / 前端 | 我产出字节流，谁把它送到打印机不归我 |
| 文件的长期存储 | `infra-storage` / `infra-attachment`（阶段五） | 本阶段直接返回字节流，不落盘 |

⚠️ **本组件是"纯函数"，这条决定了很多设计**：同样的 `template_id` + 同样的数据 = 同样的输出。由此推出——**`Render` 是全系统唯一不带 `idempotency_key` 的写命令 rpc**（没有副作用，重复调用直接重算一遍）；**没有 `command_idempotency` 表**；调用方超时了可以直接重发，不用先查状态。**别照别的组件的模板给这里加幂等键或幂等表。**

⚠️ **`data_scopes: none`**——模板是全局资产，不做行级过滤。渲染出来的内容里有业务数据，但访问控制靠**调用方**：业务组件在自己那边判完权限才来调 `Render`，不归本组件。

## 契约面与事件

**gRPC `infra.print.v1.PrintService`：** `Render`（无幂等键，见上）、`BatchGetTemplates`（防 N+1）、`ListTemplates`（游标分页）。

**REST：** `/infra/print/**` 前缀。`POST /render`（前端直接渲染）、`GET /templates`/`GET /templates/{id}`（模板管理）、`PUT /templates/{id}`（上传新版本）、`POST /templates/{id}/rollback`（回滚）、`POST /templates/{id}/preview`（预览，**不写 `print_jobs`**）。

**发布事件：** `infra.print.rendered.v1`（旁路，唯一一条，仅供未来 `infra-audit` 消费）。
**消费事件：** 无——一条都不消费（同 `infra-workflow` 的判据：消费业务事件本身就是嵌入业务语义，§6.11 明令禁止）。

## 依赖与「为什么不依赖某某」

`dependencies.components` 永远是空数组，本组件在同步图上只有入边。

- **不依赖任何业务组件**：数据由调用方整个传入。回查业务库既违反"严禁业务逻辑"也要求认识业务库结构，两条都不许。
- **不依赖 `infra-storage`/`infra-attachment`**：阶段五才有，本阶段直接返回字节流。
- **不依赖 `infra-authz`/`infra-iam-casdoor`**：`iamJwksUrl`/`authzBundleUrl` 是配置项，不是依赖边，同全项目所有组件的既有约定。

**谁依赖我（入边）：** `erp-sales`（强依赖，打印送货单）——这条边是本组件在设计阶段被判定为 `customer_fork` 而不是 slot 的原因（§5.11 硬约束：有代码、且有组件依赖它，不允许做成 slot）。

## 这个组件特有的坑

| 不许 | 症状 | 出处 |
|---|---|---|
| 给 `Render`/`RenderRequest` 加 `idempotency_key` | 没有立刻的症状，但这是对"纯函数"设计前提的侵蚀——下一个人会照着别的写命令 rpc 的模板加，而这里永远用不上 | 设计计划 §1.1 |
| 在 Jinja2 模板里塞计算型业务逻辑（比如税率、折扣公式） | 本组件选了完整 Jinja2（支持 `{% for %}` 循环），不是设计计划 §9 待决问题 3 倾向的"纯变量替换"——这条边界现在只靠代码审查守，不靠语法沙箱。往模板里塞计算逻辑不会报错，但会让模板变成业务规则的藏身处 | `backend/app/render/pdf.py` 注释 |
| 只装 `libpango`/`libcairo`/`libgdk-pixbuf` 不装字体就假设中文能渲染 | **真机实测过的真实坑**：容器里没有 CJK 字体时，WeasyPrint 退化到 `DejaVu-Serif`，`pdftotext` 提取出来是乱码且重复，不是简单的"变方框"。必须装 `fonts-noto-cjk` + `fontconfig` 并跑 `fc-cache -f` | `Dockerfile` |
| 依赖 fontconfig 的字体回退渲染中文，不在模板里显式写 `font-family` | fontconfig 在没有 `lang` hint 时默认选 Noto CJK 的 **JP** 变体而不是 **SC**（简体中文）——视觉上很接近但不是正确的字形，中文文档应该显式指定 | `README.md` 边界与禁令 |
| `put_template`/`rollback_template` 先 insert `print_template_versions` 再 upsert `print_templates` | 后者有 `REFERENCES print_templates (id)` 外键，新模板第一次保存时反过来写会撞外键违例——两个函数内部顺序已经写对，改动这两个函数时留意别颠倒 | `backend/app/repo/templates.py` |
| 用 `__file__` 反推仓库根目录去找 `migrations/` | **真机 build 镜像时验证过的真实坑**：`pip install .`（非 editable）会把 `app` 包复制进 site-packages，`__file__` 往上反推目录就不再是仓库根。改成相对 CWD（同 `component.yaml` 的既有读取约定） | `backend/app/module.py`、`backend/app/migrate.py` |
| 给 `infra_print` 连接字符串里的数据库名填 `postgres` | **真机验证时踩过的真实坑**：`docker exec be-postgres psql -U postgres` 默认连的是 `postgres` 库，而 `infra_print` schema 建在 `brickkit_db` 里；连错库会看到"schema 不存在"这种误导性报错，看起来像迁移没跑或 db-init 没做 | `docs/手册.md` 排障 |
| Dockerfile 单阶段构建，最终镜像里带着 `git`/`build-essential` | `besdk` 是 `git+https://...` 依赖，`pip install` 需要 `git` 二进制去 clone——单阶段会把编译工具链一起打进最终镜像。用多阶段构建，build 阶段装 `git`，运行阶段只留 WeasyPrint 系统依赖 | `Dockerfile` |
| 给 `print_templates`/`print_template_versions` 加 `ALTER TABLE ... OWNER TO` | 只有分区表（`print_jobs`）需要——非分区表靠 schema 的 `ALTER DEFAULT PRIVILEGES` 就有正确的 DML 权限，加了也不是错但是多余的一步（同 `infra-notification`/`infra-workflow` 的既有先例） | `migrations/001_create_print.sql` |

## 改代码前的自查

1. **我是不是在给 `Render` 加幂等键，或者在往这里补一张 `command_idempotency` 表？** 停下——本组件是纯函数，设计上就不需要，见设计计划 §1.1。
2. **我是不是在给 Jinja2 模板加限制，或者反过来在模板里塞计算逻辑？** 两个方向都停下想清楚——当前的选择是"完整 Jinja2 + 代码审查守边界"，改动前先确认这个判断是否还成立。
3. **我改的是不是 WeasyPrint 相关的容器依赖？** 停下——中文字体是真机踩过的坑，改动前重新跑一遍 CJK 渲染 + `pdftotext` 验证，不要只看"能不能装成功"。
4. **我是不是在给本组件加一条事件消费、或一条指向业务组件的调用？** 停下——本组件是纯函数，零出边零消费是硬约束（同 §6.11）。
5. **我是不是在用 `__file__` 反推路径？** 停下——改用相对 CWD，见上表的既有教训。
6. **我改的 REST/gRPC handler，权限键/`source_component`/`actor` 是不是都对得上契约？** REST 走 `besdk.scope_of().owner` 取 actor，gRPC 用调用方自报的 `source_component`，两者不能混用。
