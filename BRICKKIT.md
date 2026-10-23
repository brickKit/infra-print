# infra/print

## 组件定位

纯渲染服务：模板版本加调用方给的视图模型，渲染成 PDF。
- **不读业务数据**：视图模型由调用方按用户的权限裁剪、格式化（日期、金额、大写）之后交来，本组件按模板版本的 JSON Schema 校验（不合格答 `VALIDATION_FAILED`），只把它放进版式。
- **确定**：同一模板版本与同一数据得到逐字节相同的 PDF；字体随组件提供（Noto Sans SC），PDF 的时间戳固定。调用方记下回答的版本，重打时照传，模板改了旧单据也不变；重打标“副本”由调用方传 `copy`。
- **模板与版本**：版本永不修改；随组件提供的模板（本版只有账单 `invoice`）启动时按摘要登记，内容变了登记为新版本，不覆盖客户改过的当前版本。可以列出模板与版本、读取版本的数据 Schema、用样例数据预览（标“预览”）。
- **上限**：渲染在隔离的工作进程里，每次有时间、内存、输出上限，超出答 `RENDER_LIMIT_EXCEEDED`；同时渲染的个数有上限，排队超期答 `SERVICE_BUSY`。模板不能读文件、不能访问网络、不能下载包。

保存正式单据的输出与重打计数在调用方（附件组件以后）。本版答“能力不支持”（`CAPABILITY_UNSUPPORTED`，D75）的：在线维护模板（新版本、启用）、ZPL 标签、现场打印的任务。

## 部署前准备

- 数据库：PostgreSQL 16 及以上；schema `infra_print`，属主角色 `infra_print_owner`（只供迁移）与运行角色 `infra_print_rw`，名字来自装配项目的注册表。三者由部署方在第一次启动前建好；表由本组件的迁移建。
- 部署的服务证书（`SERVICE_TLS_*`）；身份与授权（`IAM_*`、`AUTHZ_GRPC_URL`、`DEPLOYMENT_ID`）；审计（`AUDIT_GRPC_URL`）。
- 资源：CPU 与内存是突发的。每个工作进程空闲约 150 MiB 常驻内存，地址空间上限缺省 1 GiB；同时渲染的个数缺省等于 CPU 数。进外壳（`be/py-render`）时单独预算（协议 17）。
- 镜像基于 Debian slim（Typst 只有 glibc 的二进制包）；字体约 17 MB 在镜像里。

## 依赖说明

- 必需：数据库、身份成员、授权成员。
- 可选：审计组件。
- 没有业务依赖：不调别的组件。

## 配置指南

全部键见 `component.yaml` 的 `configSchema`（由 SDK 生成，测试核对）。

| 键 | 说明 |
| --- | --- |
| `DEPLOYMENT_ID`、`IAM_ISSUER`、`IAM_JWKS_URL`、`AUTHZ_GRPC_URL`、`SERVICE_TLS_CERT_FILE`、`SERVICE_TLS_KEY_FILE`、`SERVICE_TLS_CA_FILE` | 受保护的用户面与 mTLS 的系统面要用 |
| `PG_HOST`、`PG_DATABASE`、`PG_USER`、`PG_PASSWORD`、`PG_SCHEMA`（必填） | 自己的数据库；迁移另用 `PG_OWNER_USER`、`PG_OWNER_PASSWORD` |
| `PRINT_RENDER_TIMEOUT` | 一次渲染的时限，缺省 10s；超过就杀掉工作进程 |
| `PRINT_RENDER_MEMORY_MIB` | 工作进程的地址空间上限（`RLIMIT_AS`），缺省 1024；空闲时约 300 |
| `PRINT_RENDER_OUTPUT_KIB` | 文档大小上限，缺省 3072（一个 gRPC 消息要装得下） |
| `PRINT_RENDER_WORKERS` | 同时渲染的个数，缺省 0（等于 CPU 数） |
| `PRINT_RENDER_QUEUE_WAIT` | 等一个空闲工作进程的期限，缺省 5s，超过答 `SERVICE_BUSY` |

## 契约索引

- **系统面**（本仓库 `contract/proto`，Go 模块 `github.com/brickKit/infra-print/contract/v3`，Python 包 `contract-infra-print`，标签 `contract/v3.…`）：`be.infra.print.v3.RenderService/Render`：部署内任一组件以服务身份调用，操作 `infra.print.system.render`；版本传 0 用当前版本，回答带所用的版本、版本摘要与内容摘要。
- **用户面**：`contract/openapi.yaml`。操作 `infra.print.template.read`（模板与版本的列表、读取）、`infra.print.template.preview`（预览，答 `application/pdf`）；`infra.print.template.edit` 本版不支持。模板不分法人，没有维度。
- **事件**：无。

## 外壳声明

不是外壳。可以被 Python 的渲染外壳（`be/py-render`）承载。
