# Go RPC 服务日志

新建或修改 Go／内部 Kitex 服务的日志初始化、RPC 中间件、业务日志或采集配置时读取本页。非 Go 服务沿用其现有平台日志方案。初始化 harness 只生成本文，不安装依赖或修改服务代码。

## 库与接入选择

爱学 Go RPC 服务优先采用下面已在团队服务中使用的组合。先核对目标 `go.mod`、相邻实现和公共包 API；已有等价平台接入时增量补齐，不再安装第二套 logger。

| 职责 | 包与 API | 使用方式 |
| --- | --- | --- |
| 业务日志 | `code.byted.org/gopkg/logs` | 请求内使用 `CtxInfo`、`CtxWarn`、`CtxError`，第一个参数传入 RPC context |
| 团队初始化 | `code.byted.org/pony/common/biz/logging` | `logging.InitDebugLogger(servicePSM)`，在依赖初始化和服务启动前调用 |
| Kitex 服务端日志中间件 | `code.byted.org/pony/common/biz/kitex/mw` | 按目标包支持情况注册 `LogBaseRespMW`、`LogPanicMW`、`ServiceCalledLogMw` |
| 框架采集配置 | 生成基线中的 `conf/kitex.yml` | 默认业务日志 info，rpcAccess／rpcCall trace，输出 File 与 Agent |

包路径是接入选择，不是固定版本要求。依赖版本以目标仓兼容的内部 Kitex／公共库组合为准，不复制来源项目的版本号。`logs` 与 `logs/v2` 是不同 import path，不能因间接依赖存在就混用。公共 logging／mw 包不可访问或 API 不兼容时，记录阻塞并核对团队替代入口，不自行用标准库 logger、slog 或 logrus 替换平台采集链。

## 初始化和服务端中间件

以下是接入片段，不是完整 main。`servicePSM` 必须来自目标服务已确认的身份配置；依赖、生成服务入口和退出路径保留目标仓实现。

```go
import (
    "code.byted.org/kite/kitex/server"
    "code.byted.org/pony/common/biz/kitex/mw"
    "code.byted.org/pony/common/biz/logging"
)

// 在依赖初始化和 NewServer 之前执行一次。
logging.InitDebugLogger(servicePSM)

options := []server.Option{
    server.WithMiddleware(mw.LogBaseRespMW),
    server.WithMiddleware(mw.LogPanicMW),
    server.WithMiddleware(mw.ServiceCalledLogMw),
}
// 将 options... 传给目标仓生成的 NewServer。
```

1. 核对 bootstrap 使用的 PSM 与初始化的 `servicePSM` 一致，配置或环境覆盖后仍指向同一服务。函数名含 Debug 不证明生产日志等级为 debug，等级以实际配置与运行回读为准。
2. 读取目标公共包实现，确认三项中间件的错误统计、panic 恢复、字段、脱敏和采样行为。已注册时复用，避免重复打印或重复恢复；仅凭名称不能宣称这些能力已经生效。
3. 保留内部生成桩的 `byted.ServerSuite`，在业务启动入口传入 options，不手改 `kitex_gen`。这些是服务端中间件，不是 RPC 客户端中间件。
4. 启动失败使用已初始化的统一日志路径，并在完成清理后非零退出。停机时按目标库与框架核验 Flush／关闭行为；框架已负责时不重复添加。单测中的 `logs.Flush()` 不能证明生产停机已刷盘。

完成标准：目标包和 API 可用，初始化早于依赖启动，服务标识一致，中间件只注册一次，启动失败与停机日志路径经过验证。

## 请求 context 和业务事件

RPC handler 将同一个 `ctx` 传入业务逻辑、DAO 和日志。DAO 查询使用 `WithContext(ctx)`；子任务使用继承取消和追踪信息的 context，请求内不以 `context.Background()` 截断调用链。

```go
import (
    "context"
    "time"

    "code.byted.org/gopkg/logs"
)

func recordOperationResult(ctx context.Context, operation string, started time.Time, err error) {
    durationMS := time.Since(started).Milliseconds()
    if err != nil {
        // 实际错误先映射到稳定分类，敏感底层错误不直接打印。
        logs.CtxError(ctx,
            "event=operation_completed operation=%s outcome=failed error_class=internal duration_ms=%d",
            operation, durationMS)
        return
    }
    logs.CtxInfo(ctx,
        "event=operation_completed operation=%s outcome=succeeded duration_ms=%d",
        operation, durationMS)
}
```

示例中的 operation 必须是代码定义的有限操作名，不来自用户输入。字段值含空格、换行或外部内容时使用可靠的转义／结构化编码，保持一条事件可被完整解析。

- `CtxInfo` 记录必要的受理／完成、状态转移和结果摘要；高频成功事件按团队策略采样，避免与中间件重复。
- `CtxWarn` 记录需要诊断的校验拒绝、降级和可恢复异常。预期拒绝控制频率，不把所有客户端参数错误升级为系统故障。
- `CtxError` 记录依赖失败、无法完成的操作和非预期异常。包含稳定错误分类及必要诊断摘要，不直接输出可能含 SQL、地址、凭证或用户内容的 `err.Error()`。
- recover 只作用于当前 goroutine。子 goroutine 自身处理 panic，并将错误汇聚给调用方；恢复不等于业务成功。需要栈信息时按目标日志包核验大小、敏感内容及采样。

通用事件字段包括 `event`、有限操作名、结果、错误分类和耗时。按实际链路补充 request／command ID、method、caller、LogID、build version 和 environment。优先复用框架可信上下文与构建配置；不伪造 LogID，不把模型或请求正文中的 caller 当作可信身份。框架已经提供的字段先回读验证，再决定是否补写。

身份只记录获准的脱敏值或摘要。完整 request／response、学习内容、对话、配置对象、身份凭证和数据库 DSN 不进入日志。业务错误可能封装在 BaseResp 中，验收同时检查传输 error 与业务状态，不能因 RPC error 为 nil 就记录成功。

完成标准：成功、拒绝和依赖失败事件都能定位操作结果，context 连续，字段可解析，敏感数据没有进入日志，业务失败不会被记录为成功。

## 框架配置与采集落点

保留内部生成配置的结构，按目标 Kitex 版本合并日志部分。下面是团队服务的接入形式，不覆盖目标仓其他配置：

```yaml
Log:
  Dir: log
  Loggers:
    - Name: default
      Level: info
      Outputs:
        - File
        - Agent
    - Name: rpcAccess
      Level: trace
      Outputs:
        - File
        - Agent
    - Name: rpcCall
      Level: trace
      Outputs:
        - File
        - Agent
```

生成 bootstrap 设置 `KITEX_RUNTIME_ROOT`、`KITEX_CONF_DIR` 和 `KITEX_LOG_DIR`，创建所需日志目录后 exec 服务二进制。配置中的 Dir 与 `KITEX_LOG_DIR` 优先级按目标框架核验。生产保留平台 File／Agent 采集链，Console 仅按本地调试约定启用；rpcAccess／rpcCall 的 trace 等级可能参与调用链构建，变更前核对框架规则。

`convert.ToJSONString(req)` 嵌入格式化消息不等于整条日志使用 JSON 编码。本页示例使用 key=value 消息；整行 JSON／文本格式由目标 logger 和采集器确定，实施时回读实际输出。轮转、保留期、限长、采样与上报失败策略分别核验，不能由配置中存在 Agent 推定已经具备。

## 本地与发布验收

1. 本地完成编译及日志相关测试，覆盖正常、校验拒绝、依赖失败，检查字段与脱敏。公共日志依赖无法初始化时报告阻塞，不改生产初始化来伪装测试通过。
2. 发布后使用获准 RPC 取得真实 LogID，核对同次请求的业务事件及调用链日志，关联精确运行版本和环境。
3. 默认日志 API 为空时，先列出当前实例日志文件，再按 LogID／事件检索；检查 app、rpc、run 的实际落点和轮转日期，不用硬编码当天文件名判断没有日志。
4. 核验启动错误、停机及子 goroutine 异常日志，确认采集可见且没有完整身份或业务内容。未获准故障注入时用已有安全测试证据，单列运行环境未覆盖项。

完成标准：库、初始化与采集路径有实际证据；业务日志能关联请求、版本和环境；失败与敏感字段检查通过。仅本地打印可见不算平台采集验收通过。
