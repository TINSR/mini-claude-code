# Architecture

## 核心执行链

一轮用户输入依次经过：User Hook、相关记忆追加到新输入、稳定 system 与工具池组装、上下文预算、model_gateway 调用、工具执行、新结果限长/落盘、`tool_result` 回填、Stop Hook 和记忆提取。

`tool_use` 和对应的 `tool_result` 被视为不可拆分的协议对。上下文裁剪会调整切割边界，避免产生模型 API 无法接受的历史。

## 状态所有权

| 状态 | 所有者 | 持久化位置 |
|---|---|---|
| 对话消息 | CLI 主循环 | 进程内；压缩时写入 `.transcripts/` |
| 长期记忆 | `memory_manager` | `.memory/` |
| 共享任务 | `task_manager` | `.tasks/` |
| 队友邮箱 | `team_runtime` | `.mailboxes/` |
| 定时任务 | `cron_scheduler` | `.scheduled_tasks.json` |
| Worktree | Git / `worktree_manager` | `.worktrees/` |
| 后台结果 | `background_tasks` | 进程内，完成后消费 |
| CLI 会话 | `session_manager` | `.sessions/` |

模块共享的可变对象通过 `configure_*_runtime()` 显式注入。注入原字典或列表引用，因此 CLI 后续注册的新工具对 MCP 和队友运行时仍然可见。

### 会话归属

一个任务从开始就绑定自己的 `Session` 对象：`run_session_agent(session)` 在 `finally` 中保存的就是这个对象，而不是「结束那一刻的当前会话」。`SESSION_STATE['session']` 只在持有 `agent_lock` 时换绑，所以会话切换不能插进正在运行的一轮里。

Cron 任务创建时记录 `session_id`。归属来自 `cron_scheduler.turn_session()` 这个 thread-local：`run_session_agent(session)` 会绑定当前轮次的会话，所以一轮 Cron 交付里新建的任务归属**正在运行的**会话，而不是界面上的当前会话。没有绑定时才回退到当前会话。

到期交付按归属会话分组：归属会话就是当前会话时直接追加，否则从 `.sessions/` 读出该会话、在其中执行并单独保存，同时不改写 `current` 指针。归属会话已被删除时跳过并打印原因。长期记忆不随会话切换。

`process_cron_batch()` 是可单独调用的一次交付，`queue_processor_loop()` 只是轮询它并兜住异常。交付失败的一批任务会退回队列，同一任务连续失败 `MAX_DELIVERY_ATTEMPTS`（3）次后放弃并打印。这样一次磁盘错误既不会丢任务，也不会让调度线程静默死掉 —— 线程一旦结束，之后所有定时任务都不再交付。

两点取舍要照实说：交付是**至少一次**而不是恰好一次，一批任务在部分已进入会话后失败会整批重试，那部分可能被交付两次；重试没有退避，轮询间隔 0.2 秒，所以 3 次尝试会在一秒内用完，只能扛住瞬时失败，扛不住持续故障。

`.scheduled_tasks.json` 的快照和写入都在 `cron_lock` 内完成，所以一次性任务到期和 `schedule_cron` 并发时既不会中断字典迭代，也不会用旧快照覆盖刚创建的任务。调度线程已持锁，因此传入自己的快照（`save_durable_jobs(durable_job_snapshot())`）。

`.sessions/*.json`、`.scheduled_tasks.json` 和 `.mini_claude_code/approvals.json` 都经 `atomic_io.atomic_write_text` 写入：先写同目录临时文件，`fsync` 后 `os.replace`。中途失败保留原文件，也不留临时文件。

## 工具执行与安全

所有执行路径共用 `tool_executor` 一个入口：

```text
tool_use.name
    │
    ▼
authorize_tool(block)            # PreToolUse Hooks：日志 + 权限策略
    ├── deny → tool_result 写入拒绝原因，处理器不被调用
    ▼
invoke_handler(block, handlers)  # 未知工具与异常统一成文字结果
    ├── PostToolUse Hooks
    ▼
tool_result
```

主 Agent、子 Agent、队友都调用 `execute_tool()`；主循环因为还要判断 `compact` 与后台派发，单独调用 `authorize_tool()` 和 `invoke_handler()`，但顺序和策略一致。后台任务在主线程通过 `authorize_tool()` 放行后才派发，工作线程只执行 `invoke_handler()`。

运行时自己发起的工具调用（例如队友空闲时自动认领任务）用 `tool_executor.ToolCall` 包装成同样的形状，因此也经过同一个入口。

审批能力按线程声明。`security.set_execution_context(interactive=..., base_dir=..., label=...)` 是 thread-local：主线程可以交互审批；队友线程、后台工作线程和 Cron 交付线程标记为非交互。非交互路径遇到 `ask` 时**直接拒绝并说明**，不会从后台线程读 stdin，因此不存在「后台等审批、主线程等锁」的死锁。非交互路径只能执行策略判定为 `allow` 的操作，或用户此前保存过长期许可的完全相同操作。

子进程同样不继承终端 stdin：`run_powershell` 和 Git 调用都传 `stdin=subprocess.DEVNULL`。否则后台线程里一条等待输入的命令会抢走用户在 `mycc >` 提示符下的按键，或者一直阻塞到 60 秒超时。现在这类命令会立刻失败并给出可读的错误。

`base_dir` 让权限判断落在调用方真实的工作区上：队友认领 Worktree 后，文件检查用该 Worktree 解析路径，长期许可指纹也按该工作区区分，主工作区的 `notes.md` 许可不会覆盖 Worktree 里的同名文件。

文件工具通过 `Path.resolve()` 和父目录检查阻止路径逃逸。PowerShell 使用危险命令黑名单，并为写操作保留人工审批。MCP 工具读取 `readOnlyHint`、`destructiveHint` 等注解参与授权。

需要注意的边界：`check_rules` 对 PowerShell 是关键词匹配，不在关键词表里的命令在**所有**路径都会直接放行。这是策略本身的粒度问题，统一入口只保证各路径判定一致，不代表命令白名单是完备的沙箱。

## 上下文与记忆

普通轮次保持历史不变，只追加新消息。工具结果首次进入历史前应用单结果 12,000 字符阈值和单批 24,000 字符软预算，完整结果按内容哈希落盘。超过默认 32,000 估算 token 总预算才压缩；摘要成功、未截断且能缩短上下文时才替换旧历史，同时保留近期完整工具配对。失败则保留原历史并停止本轮。

动态记忆和可用工具列表不再嵌入 system：工具由 API schema 描述，相关记忆随新用户输入追加。记忆选择/提取仍会调用模型，并单独记录用途；本次没有宣称减少这些调用的频率。

`model_gateway` 统一主 Agent、子 Agent、队友和记忆/摘要调用。工具按名称排序；直连 Anthropic 的缓存标记仅加入请求副本，DeepSeek 使用服务端自动缓存。日志记录原始 usage 和前缀变化，不把本地前缀一致性当作真实命中率。

## 并发模型

- 主 Agent Loop 运行在主线程。
- 后台工具、Cron 调度器、Cron 队列处理器和 teammate 使用 daemon thread。
- 每个 teammate 拥有独立消息历史和工具包装。
- `agent_lock` 保证主输入、会话切换和 Cron 自动交付不会同时进入主 Agent Loop。读取用户输入不持锁，Cron 才能在用户思考时交付。
- Cron 队列处理器用 `acquire(blocking=False)`，抢不到锁就下次再试；只有主线程会阻塞等待锁。审批只发生在主线程，因此持锁等待输入不会让其他线程死锁。代价是：Cron 一轮正在执行时，用户的会话命令要等它结束。
- 会话保存由 `session_manager.save_lock` 串行化。
- 邮箱与共享状态使用锁保护。

## MCP

`MCPClient` 启动外部进程，通过 stdin/stdout 发送逐行 JSON-RPC 并动态注册 `mcp__<server>__<tool>`。

生命周期按规范执行：`initialize` → `notifications/initialized` → `tools/list`。

传输层由一个读取线程独占 stdout，把每条响应按 id 交给注册了该 id 的等待者：

- 通知（没有 `id`）和没人等待的 `id` 一律丢弃并打印，不会被当成下一个请求的答案。
- 每个请求带超时（默认 30 秒，握手 15 秒）。超时后立刻注销挂起项，迟到的响应因此无人认领而被丢弃。
- stdout 读到 EOF 或读取出错时，所有挂起请求一次性失败并带上退出码，后续请求立即报错而不是再等一个完整超时。
- stdin 写入由写锁串行化，多个 Agent 线程可以共用同一个连接。
- 握手失败时 `connect_mcp` 会 `close()` 掉进程和读取线程，不注册半连接的 Server；CLI 退出时 `close_mcp_clients()` 关闭全部连接。

示例百度搜索 Server 只是一个适配器；核心客户端可继续注册其他 stdio MCP Server。`tests/fake_mcp_server.py` 是可配置的假 Server，用来覆盖通知、乱序响应、超时、进程退出和并发调用。

## 保守重构原则

`docs/legacy_main.py` 是不可变基线。`scripts/extract_*.py` 使用 AST 定位完整函数或类并移动源文本，函数内部只进行模块接线必需的替换。测试校验基线 SHA-256，防止整理项目时无意改写原实现。

当前模块已有后续行为修复，不应直接重跑历史提取脚本覆盖源码。`scripts/extract_cron_module.py` 等脚本描述的仍是拆分当时的接线方式（例如 Cron 绑定共享 `messages` 列表），与现在的会话归属实现已经不同。
