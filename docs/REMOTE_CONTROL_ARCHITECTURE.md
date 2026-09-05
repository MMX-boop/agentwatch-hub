# AgentWatch Bridge：第二阶段设计草案

**状态：仅设计，未实现。** 0.2.0 只提供出站完成通知。这里的配置、输入对象、命令、入站适配器和任务管理器当前都不可用。不能通过 QQ / 飞书消息启动 Agent、shell、停止任务或批准操作。

## 现有结构与扩展点

```text
Codex 桌面版本地任务 / Claude Code
  → 原有 notify / Stop / StopFailure 回调
  → normalize + 事件去重检查
  → sanitize / summary / persona（可选 LLM，只生成一次）
  → NotificationMessage(title, body, generated)
  → ChannelRouter
      ├ BarkChannel
      ├ OneBotChannel（HTTP 出站，通道名 qq）
      └ FeishuWebhookChannel（群机器人出站，通道名 feishu）
  → 至少一条投递成功后记录 sent.json
```

`channels/base.py` 定义轻量消息模型和 `NotificationChannel` Protocol；`router.py` 的通道注册表管理选择、状态和异常隔离。增加出站通道时，实现协议并注册，增加对应配置与测试，不改 `normalize()`、人格生成或去重算法。通道只转换格式与投递，不解释消息里的指令。

`providers.send_bark()` 保留为兼容入口。原 Hook argv、安装/卸载以及 previous Codex notify forwarding 不变。当前每个回调完成投递后退出；没有服务端、数据库、队列或入站监听。

## 第二阶段数据流

```text
QQ / Feishu
  → Inbound Adapter
  → AgentWatch Bridge
  → Auth / ACL
  → Command Router
  → Task Manager
  → Agent Adapter（Codex / Claude Code）
  → Completion Event
  → persona / NotificationMessage
  → Outbound Router
  → 原会话
```

建议保持 Bridge 为显式启动的独立可选进程。现有通知用户无需常驻服务，安装 Hook 也不会自动启用远程控制。

### 入站通道

- **QQ**：NapCat 作为 OneBot v11 Reverse WebSocket 客户端连接到 Bridge；独立 `OneBotInboundAdapter` 处理鉴权、消息事件和重连。OneBot 是协议，NapCat 是其实现之一，不把产品名写死进核心逻辑。QQ 官方 Bot API 可以另增 Adapter。
- **飞书**：`FeishuInboundAdapter` 使用应用机器人、App ID / App Secret、事件订阅 `im.message.receive_v1` 和长连接或受校验的事件回调。回复通过独立 `FeishuAppChannel`，需要 `im:message:send_as_bot` 等实际申请的权限。现有 Webhook 群机器人不负责接收命令，不假装支持私聊。

真正实现前要重新核对两个平台的协议、凭证、权限及重试行为；本轮没有引入 WebSocket / Bot 框架依赖。

### 统一入站对象（建议）

```python
InboundMessage(
    channel="qq",
    conversation_id="opaque-conversation-id",
    sender_id="opaque-sender-id",
    message_id="platform-message-id",
    text="/run 修复 README 中的安装说明",
)
```

还应由经过认证的 Adapter 附加：会话类型、Bot/租户身份、可信接收时间、原事件时间和可信回复路由。不要从消息正文中读取或覆盖 `sender_id`、项目路径、回复目标或权限。

### 身份与任务关联

Task Manager 为任务生成 `aw_...` ID，关联已授权的发送者、Agent、项目 ID、原会话路由与 Agent 自身 thread/session ID。通知回到创建任务的原会话，不能让用户输入任意收件人或将结果默认广播给所有当前通知目标。

当前静态 `QQ_TARGETS` 和飞书群 Webhook 是通知目的地配置。未来增加受信任的 `ReplyRoute` / `TaskContext` 作为可选路由元数据，不把私人会话路由编码到正文，也不改变旧 Hook 的既有调用协议。

## 默认安全模型

以下仅为未来配置设计，当前 `.env.example` 不包含它们，当前程序不会因填写它们而获得远程执行能力：

```dotenv
REMOTE_CONTROL_ENABLED=false
ALLOWED_QQ_USERS=
ALLOWED_QQ_GROUPS=
ALLOWED_FEISHU_USERS=
ALLOWED_PROJECT_ROOTS=
```

1. **默认关闭与拒绝**：显式启用 Bridge 后仍需配置 ACL。空白名单不允许任何人执行任务。群聊必须同时满足发送者和群白名单；加入群的普通成员不能继承 Bot 权限。
2. **认证先于解析**：验证 OneBot 连接凭证；飞书验证平台要求的身份/签名/事件凭证，并核对租户与 Bot。远程传输使用 TLS。消息中的自称身份和 LLM 判断都不构成授权。
3. **防重放与防循环**：以平台、租户/Bot、会话与 message ID 去重，拒绝过期事件，验证回调时间窗口。过滤机器人自身和其他机器人的回声。重连、平台重试不得重复启动任务。
4. **项目目录白名单**：用户选择预注册项目 ID，服务端映射到允许的根目录。对最终 cwd 做规范化、resolve、盘符/UNC 检查以及符号链接和 Windows junction 逃逸检查。先检查路径属于允许根目录，再启动；不能直接把聊天里的路径传给 Agent。
5. **执行边界**：显式 executable + argv + cwd，不使用 `shell=True`。Agent 可执行程序、参数与工作目录来自受控配置。`/run` 后面的文字是 Agent 的任务输入，不是 shell 命令，也不能变成额外命令行选项。
6. **保留 Agent 权限机制**：继续使用 Codex / Claude Code 的 permission、sandbox 与批准流程。不得默认加入 `--dangerously-skip-permissions` 或 `--dangerously-bypass-approvals-and-sandbox`，不得通过 Bridge 静默批准操作。
7. **状态与停止权限**：查询、停止只允许任务所有者或明确配置的管理员。`/stop` 只定位该任务绑定的进程/会话，先温和中断，不能终止电脑上所有同名 Agent 进程。
8. **资源限制**：限制每用户/会话速率、并发任务数、队列长度、运行时间、输入和输出体积。任务超限不自动扩大权限。多项目任务需独立 cwd、身份和状态。
9. **批准绑定**：未来若支持手机批准，必须绑定请求 ID、任务、操作和一次性有效期，再次核验操作者身份，不能把泛化的“同意”当作无限期授权。
10. **最小数据与审计**：记录任务状态、操作者、授权结果、时间和脱敏错误类别。凭证不进入 repr/log，日志默认不保存完整对话与代码。限制日志保留时间和权限，结果只发往绑定会话。

## 命令协议（草案）

| 命令 | 预期行为 |
| --- | --- |
| `/status` | 查询 Bridge 和当前用户可见任务状态 |
| `/projects` | 列出 ACL 允许的项目 ID，不直接暴露绝对路径 |
| `/agent codex` / `/agent claude` | 选择允许使用的 Agent，不改变权限级别 |
| `/run 修复 README 中的安装说明` | 在已选允许项目启动一个任务 |
| `/task <id>` | 查询有权限访问的任务摘要 |
| `/stop <id>` | 中断有权限停止的任务 |

项目选择可以通过明确的项目参数或受控选择器完成；没有选中合法项目时拒绝执行，不能猜测 cwd。LLM 可以辅助理解意图，但最终命令、ACL、Agent 和项目的选择都必须通过确定性校验。

### 通知示例（仅展示预期格式）

```text
🚀 Task started
Agent: Codex
Project: agentwatch-hub
Task ID: aw_example
```

```text
✅ Task completed
Agent: Codex
Project: agentwatch-hub
Task ID: aw_example
修改 5 个文件
Tests: 37 passed
```

上述文件数与测试结果是示例，不是当前版本已采集的指标。未来必须从可验证的执行结果取得这些字段；仅收到回合完成事件时，不能臆测“测试通过”或把停止/失败报告为成功。

## 任务生命周期与交付语义

建议状态：`queued → running → completed / failed / cancelled`，另有 `waiting_for_approval`。状态变迁必须由已关联的执行事件驱动；Agent 自己的最终文字不构成授权或真实执行证据。

入站消息去重、任务启动幂等和出站通知去重要分别处理。第一阶段仅提供事件级出站去重：部分通道成功后不会重发整个事件，失败通道没有持久重试队列。未来若需要可靠的原会话回复，应设计按收件目标记录的送达结果和有限重试；不要承诺 exactly-once，因为远端收到了而连接超时的情况无法单凭 HTTP 判断。

Bridge 重启后的任务恢复、任务所有权持久化和中断协议需要在第二阶段单独设计；不能用“重试 /run”代替恢复正在运行的任务。本轮不新增数据库。

## 分步落地与验证

1. 先做只读命令 `/status`、`/projects`，验证入站认证、ACL、防重放和原会话回复。
2. 用假的 Agent Adapter 测试任务生命周期与并发；此时仍不启动 shell 或真实 Agent。
3. 仅在测试项目中启用真实 Agent，保留其原有 sandbox / approvals。先核对 Codex 桌面会话能否被目标接口关联，不把 CLI 启动能力等同于控制既有桌面会话。
4. 补充攻击与故障用例：伪造用户、未授权群、路径穿越、符号链接逃逸、重复事件、掉线重放、Bot 回声、跨任务停止、过期批准、凭证泄露和超时恢复。
5. 实机验证成功后再开放可选 Bridge，不影响只用出站通知的用户。

协议参考：[OneBot v11](https://github.com/botuniverse/onebot-11)、[NapCat 网络配置](https://napneko.github.io/config/basic)、[飞书开放平台](https://open.feishu.cn/document/home/index)。
