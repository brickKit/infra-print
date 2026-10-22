# infra-print 的契约

`infra/print` 发布的契约包：系统面的 protobuf 与用户面的 OpenAPI；没有事件。放在组件仓库的 `contract/` 子目录里单独发布（标签 `contract/v3.…`）。

依据：装配项目 [be-assembly-standard](https://github.com/brickKit/be-assembly-standard) 的协议 10（打印与渲染）、协议 17（资源上限）；组件的 `docs/design.md`。

## 内容

| 位置 | 是什么 |
| --- | --- |
| `proto/be/infra/print/v3/print.proto` | `RenderService.Render` |
| `gen/go/be/infra/print/v3/` | Go 代码，包 `printv3`；模块 `github.com/brickKit/infra-print/contract/v3`（出账等 Go 组件调用时用） |
| `python/` | Python 包 `contract-infra-print`，模块 `be.infra.print.v3`（本组件自己用） |
| `openapi.yaml` | 用户面：模板与版本的读取、预览；每个路由的操作写在 `x-operation` |

## 要点

- **渲染是确定的**：同一模板版本与同一数据得到逐字节相同的输出；`version` 传 0 用当前版本，回答里带实际用的版本，调用方记下、下次照传。
- **数据由调用方备好**：已按调用者的权限裁剪、已格式化；先按版本的 JSON Schema 校验，不合格答 `VALIDATION_FAILED`。
- **上限**：时间、内存、输出大小（缺省 3 MiB，一个 gRPC 消息装得下），超出答 `RENDER_LIMIT_EXCEEDED`。
- **能力不支持**（D75）：ZPL、模板的在线维护。
