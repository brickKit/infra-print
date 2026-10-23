# infra/print

打印组件的代码（Python）。用法与边界在 `BRICKKIT.md`，为什么这样设计（术语、数据、规则、未决问题）在 [docs/design.md](docs/design.md)；跨组件的规则在装配项目 be-assembly-standard 的协议 10（打印与渲染）、协议 17（资源上限）。

## 代码地图

| 位置 | 内容 |
| --- | --- |
| `infra_print/__init__.py` | 组件定义：身份、端口、渲染上限的配置键、资源与操作 |
| `infra_print/module.py` | 用户面（模板与版本的列表、读取、预览）、系统面（`RenderService.Render`，部署内任一组件可调）、启动时登记随组件提供的模板 |
| `infra_print/templates.py` | 随组件提供的模板（`infra_print/bundled/`）、版本的摘要、启动时登记（`register`）、版本的读取与缓存、视图模型的校验 |
| `infra_print/renderer.py` | 渲染的工作进程池：同时渲染的个数、排队期限、超时杀掉、死了换新、输出上限 |
| `infra_print/worker.py` | 一个工作进程：先设地址空间上限，再载入 Typst；空的根目录、只有随带的字体、只读的包缓存、不通的代理 |
| `infra_print/bundled/invoice/` | 账单模板：`template.typ`、视图模型的 JSON Schema（`schema.json`，与 erp/billing 的视图模型一致）、`meta.json` |
| `infra_print/fonts/` | Noto Sans SC（OFL），模板版本的一部分 |
| `infra_print/migrations/` | 表 |
| `contract/` | 本组件发布的契约，单独的包：`contract/proto/`、`contract/python/`、`contract/openapi.yaml` |
| `docs/design.md` | 设计 |

| 想改 | 从这里开始 | 然后 |
| --- | --- | --- |
| 账单的版式 | `infra_print/bundled/invoice/template.typ` | 内容变了就是新版本（启动时登记）；视图模型变了同时改 `schema.json` 与 erp/billing 的 print.go |
| 渲染上限 | `__init__.py` 的配置键 | `renderer.py`；`tests/test_render.py` 的上限一段 |
| 模板能碰到什么 | `worker.py` | `tests/test_render.py` 的“不读文件、不下载包” |
| 加一个随带的模板 | `infra_print/bundled/` 下一个目录，三个文件同账单 | 调用它的组件的视图模型 |
| 加字段、加接口 | 装配项目 be-assembly-standard 规范里的“改动配方” | `docs/design.md` 的数据与命令 |

## 构建与测试

- `uv run pytest -q`（`make test`）：清单的 `configSchema` 与事件、OpenAPI 与路由（`besdk.manifest`）、合并安全、渲染的确定性与各项上限、模板的隔离。
- `uv run pytest -q -m integration`（`make test-integration`）：真实的 PostgreSQL，例如 `BE_SDK_TEST_PG_ADMIN=postgres://postgres:<口令>@localhost:5432/postgres`（口令是装配项目 `.env` 的 `POSTGRES_PASSWORD`）；be-sdk-python 的 `besdk.testing` 是测试部署；库 `brickkit_test_db`，schema `infra_print_it` 每次开始时删掉再建，跑完留着；系统面以 `erp/billing`、`erp/sales` 的服务身份调用。
- `brickkit lint`

## 设计取舍

- **Typst，不是 WeasyPrint**：同一张账单实测快约 80 倍、内存约四分之一，自己就封闭（不读系统字体、不能出根目录）；代价是模板不是 HTML。实测见 `docs/design.md` 的“渲染引擎”。
- **渲染在工作进程里**：不占事件循环，崩溃不拖垮成员。工作进程只开一个 Rayon 线程：Typst 的线程池让地址空间到约 1 GiB，一个线程同样快、约 300 MiB，`RLIMIT_AS` 才能起作用（缺省 1 GiB）；分配失败时 Rust 直接中止进程，池把它当作超出上限并换一个新的。
- **确定性**：字体随组件、模板只读视图模型、PDF 的时间戳固定、视图模型按键排序后交给 Typst；同一版本同一数据逐字节相同（跨进程也是）。
- **模板碰不到外面**：根目录是空的，包缓存只读（`@preview` 的包不会下载），代理不通。

## 易错点

- 不要在模块里读进程环境：工作进程的环境是固定的几项（`renderer.py` 的 `WORKER_ENV`）。
- 不要在事件循环里渲染：经 `Renderer.render`。
- 镜像用 Debian slim：Typst 只发 glibc 的轮子；`wget` 要装上，平台的健康检查经 `/bin/sh` 与 `wget`。

## 改代码前自查

- 改了契约（`contract/`）：proto 改后重新生成、发布 `contract/v…`，再改这里的依赖；OpenAPI 与路由由测试核对。
- 改了版本号吗？每次改动都升版本。

<!-- brickkit:managed:begin lang=zh -->
## BrickKit

这是一个 BrickKit 组件：平台只读 `component.yaml`。它依赖的规则：

- `configSchema` 的键就是代码读的环境变量名。不能用保留名：`COMPONENT_ID`、`COMPONENT_VERSION`、`PORT`、`BRICKKIT_SERVED_MEMBERS`、`BRICKKIT_SERVED_MEMBERS_CONFIG`，以及任何 `*_ENDPOINT`。
- 依赖写精确版本。依赖的地址以 `<ID>_ENDPOINT` 注入；缺席的可选依赖根本没有这个变量，读的时候要带兜底。
- `/healthz` 只查本进程，不查依赖。迁移命令用同一个镜像跑，遇到不认识的参数必须直接失败；`local.migrationCommand` 从源码跑同一条迁移，给 `mode: local` 用。
- `BRICKKIT.md` 会随版本进入每个使用它的项目，在那里是脱离仓库单独读的：跟代码一起改，不放相对链接。
- 发版：改 `metadata.version`，提交、推送，`brickkit release`。`brickkit lint` 会检查清单和这些文档——在项目里、在这个目录下跑，只查这个组件（`--all` 查整个项目）。
- 完整规则在 `brickkit-component` 技能里（装了技能的项目或仓库根目录下的 `.claude/skills/brickkit-component/SKILL.md`；`brickkit skills update` 会装上）；参数问 `brickkit <命令> --help`；BrickKit 自己的文档用 `brickkit docs` 看。
<!-- brickkit:managed:end -->
