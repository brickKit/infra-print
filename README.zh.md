[English](README.md) · [中文](README.zh.md)

# infra/print

纯函数的渲染服务：模板 ID 加 JSON 数据，产出 PDF 字节流或 ZPL 指令流，不含业务逻辑。

## 在项目里使用

```bash
brickkit add infra/print@2.0.0
brickkit up
```

先看 BRICKKIT.md 的"部署前准备"（PostgreSQL schema 与登录角色、NATS、授权与 JWKS 地址，以及托管它的外壳必须带的字体）。

## 文档

| 想知道 | 读 |
|---|---|
| 它做什么、不做什么，怎么配置，要准备什么 | [BRICKKIT.zh.md](BRICKKIT.zh.md) |
| gRPC、REST 与事件契约 | [contracts/](contracts/) |
| 为什么这样设计：边界、数据、渲染沙箱、未决问题 | [docs/design.zh.md](docs/design.zh.md) |
| 依赖什么（没有）与配置键 | [component.yaml](component.yaml) |
| 怎么开发：代码地图、测试、易错点 | [AGENTS.zh.md](AGENTS.zh.md) |

## 开发

Python 3.12、FastAPI、`grpc.aio`、asyncpg、yoyo-migrations、Jinja2 与 WeasyPrint，基于 be-sdk-python。`make venv` 建 `.venv`；测试要真实的 PostgreSQL（`TEST_PG_DSN`），PDF 相关的断言还要本机有 `pdftotext` 与 Noto CJK 字体。确切命令与成功的样子见 AGENTS.zh.md 的"构建与测试"。在 BrickEnterprise 项目根目录 `make verify ID=infra/print FOCUS=1` 真机跑一遍：容器形态与本机进程各一次，跑完收尾。
