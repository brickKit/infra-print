# infra-print

组件 `infra/print`：纯渲染服务。模板版本加调用方给的视图模型，渲染成逐字节确定的 PDF（Typst，在隔离的工作进程里，有时间、内存、输出上限）；模板与版本的读取、预览。使用说明见 `BRICKKIT.md`，修改说明见 `AGENTS.md`，设计见 `docs/design.md`。

旧代码在 `legacy/v2` 分支，按终局设计从零开始（D59），版本从 `3.0.0` 起（A110）。

## 在项目里使用

在装配项目里 `brickkit add infra/print@<版本>`，再按 `BRICKKIT.md` 的“部署前准备”配置。

## 文档

| 问题 | 看哪里 |
| --- | --- |
| 它做什么、怎样部署与配置 | `BRICKKIT.md` |
| 为什么这样设计：术语、数据、规则、未决问题 | `docs/design.md` |
| 怎样改它 | `AGENTS.md` |
| 契约 | `contract/README.md` |
| 字体的来源与许可 | `infra_print/fonts/README.md` |

## 开发

见 `AGENTS.md` 的“构建与测试”。
