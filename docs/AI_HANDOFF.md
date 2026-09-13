# 给接手 AI 的交接文档

交接日期：2026-09-13（第二次）。本文记录已有实现与后续任务。上一版交接列出的三个 P0/P1 代码缺口已经修复并有回归测试；剩下的两项需要用户提供付费预算或仓库地址，无法由 AI 单方面完成。

最近一次完整验证：**2026-09-13**，153 项离线测试通过、Ruff / compileall 通过、总覆盖率 80%、wheel 构建与安装冒烟通过。详见第 4 节。

## 1. 用户目标与当前范围

用户自行学习实现了仿 Claude Code 的 coding agent，最初反馈缓存命中低、token 消耗高，以及 Windows subprocess 的 GBK 解码异常。用户随后授权修改，并要求检查项目距离适合在 GitHub 个人主页展示还缺什么。

用户特别关心“提高缓存命中是否反而增加总 token 和费用”，必须区分可复用前缀、服务端命中量、总输入量与实际费用，**不能承诺尚未测量的省钱比例**。

当前主工作目录就是本仓库根目录。

- 活跃版本是这里的分文件项目，源码位于 `src/mini_claude_code/`。
- 父目录中的 `my_claude_code/main.py` 是原始单文件版本，不要把后续修复误写到那里。
- 先阅读本文件、`README.md`、[ARCHITECTURE.md](ARCHITECTURE.md)、[CACHE_POLICY.md](CACHE_POLICY.md)、[RELEASE_READINESS.md](RELEASE_READINESS.md)，再核对实际代码和用户后续指示。
- 用户已授权本地修改；交接没有指定新的远程仓库，也没有授权付费基准调用。无需为普通本地修复反复确认；真实 API 基准先明确服务商、模型及可用预算，发布先明确自己的仓库目标。

## 2. Git 与文件保护

- 最近检查时，整个 `mini-claude-code/` 在父仓库仍为未跟踪目录（`?? mini-claude-code/`）。普通 `git diff` 看不到这些文件里的修改；未跟踪不代表可删除。
- 父仓库 origin 为 `https://github.com/shareAI-lab/learn-claude-code.git`，是学习来源上游，不是已确认的个人发布目标。没有创建远程仓库、提交或推送。
- 不要执行清理未跟踪文件、重置工作区或覆盖已有修改的操作。
- `docs/legacy_main.py` 是不可改写的历史基线，测试校验其哈希。不要修改快照或调整预期哈希绕过测试。
- `scripts/extract_*.py` 是历史提取脚本，不是升级脚本；重新执行会覆盖新实现。这些脚本里的接线方式（例如 `configure_cron_runtime(agent_loop, messages)`）已经和现在的源码不同，**以 `src/` 为准**。
- 不要输出或提交 `.env`、API key、会话、记忆、转录、长期审批、真实任务输出和本地审计产物。usage 日志不包含提示词正文，不意味着其他持久化文件也已去敏。

## 3. 已经完成的实现：不要重复推翻

### 统一工具授权（2026-09-13）

`tool_executor.py` 是所有执行路径唯一的授权与执行入口：`authorize_tool()` 跑 PreToolUse Hook（日志 + 权限策略），`invoke_handler()` 执行处理器并跑 PostToolUse Hook，未知工具和处理器异常统一成文字结果。主 Agent、子 Agent、队友都用它；主循环因为还要判断 `compact` 与后台派发，分别调用两个函数，但顺序与策略一致。后台任务在主线程放行后才派发，工作线程只执行 `invoke_handler()`。

审批能力按线程声明：`security.set_execution_context(interactive=, base_dir=, label=)` 是 thread-local。主线程可交互审批；队友线程、后台工作线程、Cron 交付线程标记为非交互。**非交互路径遇到 `ask` 直接拒绝并说明原因，绝不从后台线程读 stdin** —— 这是刻意避免「后台等审批、主线程等锁」的死锁，不要改成排队到主线程审批而不重新设计锁。

`base_dir` 让权限判断落在调用方真实的工作区：队友认领 Worktree 后文件检查按该 Worktree 解析路径，长期许可指纹也按工作区区分，主工作区的 `notes.md` 许可不会覆盖 Worktree 里的同名文件。

运行时自己发起的调用（队友空闲自动认领任务）用 `tool_executor.ToolCall` 包装后同样过入口。`run_powershell` 和 Git 调用都传 `stdin=subprocess.DEVNULL`：否则后台线程里等待输入的子进程会抢走用户在 `mycc >` 下的按键，Python 层的 `interactive` 标记管不到子进程。改这两处时不要把它去掉。

功能取舍已写入 README：队友实质上是「只读 + 已保存长期许可」。不要在文档里把它说成完整的可写权限。

**已知策略边界**（不是这次引入的，但不要在文档中掩盖）：`check_rules` 对 PowerShell 是关键词匹配，不在关键词表里的命令在所有路径都自动放行。统一入口只保证各路径判定一致，不代表这是沙箱。

### 会话归属与原子写入（2026-09-13）

任务从开始就绑定自己的 `Session` 对象：`cli.run_session_agent(session)` 在 `finally` 里保存的就是这个对象。`cli.SESSION_STATE['session']` 只在持有 `agent_lock` 时换绑，会话命令也移进了锁内。任务所属会话不是当前会话时，保存用 `mark_current=False`，不抢 `.sessions/current` 指针。

`CronJob.session_id` 记录归属会话，来自 `cron_scheduler.turn_session()` 这个 thread-local：一轮 Cron 交付里新建的任务归属**正在运行的**会话，不是界面上的当前会话。`deliver_fired_jobs()` 按归属会话分组交付，归属会话被删除则跳过并打印。旧的 `.scheduled_tasks.json` 缺少该字段时按空值加载。

`atomic_io.atomic_write_text()` 用同目录临时文件 + `fsync` + `os.replace`，用于 `.sessions/*.json`、`.scheduled_tasks.json`、`approvals.json`。失败保留原文件且不留临时文件。`.scheduled_tasks.json` 的快照和写入都在 `cron_lock` 内，调度线程已持锁所以传自己的快照。

一次交付是 `process_cron_batch()`，`queue_processor_loop()` 只轮询它并兜住异常。失败的批次退回队列，同一任务连续 `MAX_DELIVERY_ATTEMPTS`（3）次失败才放弃。**不要把兜底 `except` 去掉**：这个线程一旦结束，之后所有定时任务都静默不再交付。已知取舍：交付是至少一次（整批重试可能重复交付已成功的任务），重试无退避（0.2 秒轮询下 3 次尝试一秒内用完）。要改成恰好一次需要按任务记录交付状态，不是把重试次数调大。

代价：Cron 一轮正在执行时，用户的会话命令要等它结束。Cron 队列处理器用 `acquire(blocking=False)`，只有主线程会阻塞等锁，所以不会死锁。

### MCP 传输层（2026-09-13）

生命周期改为 `initialize` → `notifications/initialized` → `tools/list`。一个读取线程独占 stdout，按 id 把响应交给注册了该 id 的等待者；通知（无 `id`）和无人等待的 id 一律丢弃并打印。每个请求带超时（默认 30 秒，握手 15 秒），超时立刻注销挂起项，迟到响应因此无人认领。stdout EOF 或读取出错时所有挂起请求带退出码一次性失败，后续请求立即报错。stdin 写入加写锁，支持多线程共用连接。握手失败时 `connect_mcp` 会 `close()` 掉进程和读取线程、不注册半连接的 Server；CLI 退出调用 `close_mcp_clients()`。

`tests/fake_mcp_server.py` 是可配置的假 stdio Server（`normal` / `silent_init` / `hang_on_call` / `late_first_call` / `crash_on_call` / `concurrent`）。`normal` 模式会在握手时故意发一条通知和一个没人等待的 id，并能回报它实际观察到的方法顺序 —— 测试断言的是真实通信行为，不是内部常量。

### 缓存与预算（2026-09-12，未改动）

`model_gateway.py` 的统一 `call_model` 入口覆盖主 Agent、子 Agent、队友、摘要及记忆调用。工具按名称排序，system 不含动态记忆索引和工具名称列表；相关记忆仍可追加到新用户消息。

普通轮次保留旧消息原样，只追加新消息。原先滚动改写旧工具结果的路径已停用；兼容函数 `micro_compact` 保留但不再执行改写。

新工具结果在**首次写入历史前**处理：单项 12,000 字符、批次 24,000 字符软限制、预览 2,000 字符，完整内容落盘到所属工作区 `.transcripts/tool-results/`，采用内容哈希文件名。不得为了满足软限制丢掉工具调用 ID 对应关系。

`CONTEXT_TOKEN_BUDGET` 默认 32,000，最低 16,000，包含输出预留。估算是保守字符启发式，不是真实 tokenizer。超过预算才压缩：通常保留最近 6 条消息并调整边界保持工具调用/结果配对；响应式压缩保留最近 2 条。摘要失败、截断、为空或未缩短时保留原历史并停止当前轮次。

服务商适配：直连 `api.anthropic.com` 自动设置 ephemeral 缓存断点；DeepSeek 用服务端自动缓存；未知兼容接口默认不加参数。`PROMPT_CACHE_MODE=anthropic` 可显式选择，`off` 仅停止新增显式标记。

### 可观测性（2026-09-12，未改动）

网关在工作目录 `.transcripts/usage_<time_ns>.jsonl` 写入原始 usage、调用目的、模型、耗时、stop_reason、system/tools 哈希及相邻请求前缀结构指标，不记录密钥或消息正文。日志写入失败不会让已成功的模型请求被重试。

调用目的包括 `main`、`subagent`、`teammate`、`summary`、`memory_select`、`memory_extract`、`memory_consolidate`。`usage_report.py` / `mycc-usage` 按模型、服务商、目的汇总原始计数，缺失字段保持未知。

### 编码、入口和其他（2026-09-12，未改动）

- Shell 捕获 bytes，再尝试 UTF-8-sig / GB18030 解码，最后替换并告警；PowerShell 设置 UTF-8 输出编码。不要推断所有其他 Git subprocess 路径都已覆盖。
- 文件读取默认 200 行，支持从 0 开始的 offset 和最多 2,000 行 limit。超长单行仍是限制。
- CLI 延迟创建客户端，`--help`、`--version` 无需密钥；读取工作目录 `.env`，既有环境变量优先。
- wheel 暂不包含示例 Skills/MCP 资源，README 已说明手动复制方式。

## 4. 验证证据与不能宣称的结论

以下是 **2026-09-13 实际运行的结果**：

| 检查 | 结果 |
| --- | --- |
| 环境 | Windows，Python 3.13.12；项目声明 Python >= 3.11 |
| pytest | 153 项通过（上一版 114 项），均为离线测试 |
| Ruff / compileall | 通过 |
| 总覆盖率 | 80%（上一版 72.57%） |
| 分模块覆盖率 | tool_executor / atomic_io 100%，session_manager / background_tasks / shell 95%，model_gateway 91%，security 90%，context_manager 88%，mcp_client 87%，cron_scheduler 82%，CLI 63%，team_runtime 63% |
| 离线缓存回放 | 与上一版完全一致：2/9 对 9/9，累计 119,326 对 228,330 字符 |
| wheel | 构建成功，29 个归档条目（新增 `tool_executor.py`、`atomic_io.py`） |
| 安装冒烟 | 工作区外从目标安装目录加载包，`--help` / `--version` / `mycc-usage` 可用；缺配置退出码 2 |
| 打包内容 | 只含 24 个源码模块和 dist-info；无 `.env`、会话、记忆、转录、审批文件 |
| 安装边界 | 复用已有 venv 依赖，不等于全新 Windows 从零安装 |
| 秘密扫描 | 沿用 2026-09-12 的有限规则扫描结果，本次未重跑 |

**环境注意事项**：本机若 `%LOCALAPPDATA%\Temp\pytest-of-*` 或仓库内 `.pytest_cache` 的 ACL 拒绝访问，直接 `pytest` 会在 fixture 阶段报 `PermissionError: [WinError 5]`，与代码无关。第 6 节给了绕开方式。

[离线回放结果](cache_replay_result.json)：10 次合成请求的 9 组相邻比较，旧策略完整保留前缀 2/9，新策略 9/9；累计序列化消息字符分别为 **119,326 和 228,330**。**9/9 是本地结构指标，不是服务端命中率，别写成“命中率 100%”。**

[真实付费对照](cache_live_result.json)已经跑过（2026-09-13，DeepSeek `deepseek-v4-flash`，10 轮/组，两次运行数字完全一致）：命中率 31.1% → **82.2%**，未命中（计费）token 25,459 → **11,021**，但提示词总量 36,979 → 61,965。字符数与离线回放完全对得上。**盈亏平衡点是缓存命中单价低于未命中单价的 36.6%**：1/10 折扣时新策略约 0.61 倍费用，1/2 折扣时反而贵 1.17 倍。引用这些数字时必须连边界一起写：单一服务商模型、合成重复文本、thinking 关闭、`max_tokens=32`、未测任务质量。

回归测试也不能过度宣称：它们验证的是各执行路径的**判定一致性**和传输层行为，不是完整安全审计，也不是真实并发压力测试。

## 5. 下一步按优先级完成

### P1：真实任务基准（缓存部分已完成，任务质量部分待做）

**缓存对照已完成**，见第 4 节和 `examples/cache_live_benchmark.py`。剩下没做的是**真实任务**基准：选一个小型可丢弃测试项目，展示「定位失败测试 → 修改代码 → 测试通过」，保存去敏的 diff 和 usage。

任务基准要报告的是缓存基准没覆盖的部分：任务完成质量、真实输出 token（缓存基准把 `max_tokens` 压到 32，没测输出）、摘要与记忆调用占比、冷/热缓存条件下的总费用。保留完整历史是否真的让模型答得更好，目前**没有任何数据**——缓存基准只证明了它更便宜（在命中折扣够深的前提下），没证明它更好用。

跑真实任务时注意一个已知坑：`deepseek-v4-flash` 默认开 thinking，要求 assistant 历史把 `thinking` 块原样传回。主循环存的是 `response.content` 原对象，所以没问题；但任何**人工构造** assistant 消息的代码（基准脚本、测试夹具）都会撞 400 错误，要么带上 thinking 块，要么传 `thinking={'type': 'disabled'}`。

### P1：GitHub 展示与发布准备（需要用户提供仓库地址）

1. 确定用户自己的仓库目标。当前 CI 在子目录 `.github/workflows/`，父仓库不会发现它；独立仓库可直接使用，否则需在父仓库根级配置工作流及 working-directory。
2. 在干净 Windows 环境验证安装，明确示例资源如何获得；必要时添加资源包或初始化命令。
3. 制作去敏的终端截图/短 GIF 和可复现小任务，用真实结果完善 README，填写真实项目 URL / About / Topics。
4. 核对沿用代码、来源致谢和版权许可；现有独立 LICENSE 仅署名 TIM，不能只凭文件存在就判定继承声明完整。
5. 补维护入口：CHANGELOG、Issue/PR 模板、版本说明、已知限制。
6. 发布前检查实际候选文件和 Git 状态，禁止把本地数据、审计日志和密钥推上去。公开定位为实验性学习项目，不承诺生产级沙箱或 GitHub Trending。

### P2：可以不等用户就继续做的事

- 提升 CLI 与 team_runtime 覆盖率（各 63%）：Ctrl+C / EOF 退出、失败输出恢复、队友 `idle_poll` 与关机协议的完整生命周期、真实临时 Git 仓库的 Worktree 测试。
- 把 PowerShell 的关键词匹配换成更明确的策略（例如默认询问、显式放行表），或至少在提示词里让模型知道边界。
- 继续拆 `cli.py`（仍混合工具 schema、运行时接线和交互），避免跨模块全局状态。
- 用已有 usage 日志定位记忆调用和续写重生成的费用占比，再决定是否优化。

## 6. 本地验证命令

PowerShell，优先复用项目虚拟环境；先确认路径仍存在：

```powershell
Set-Location '<this-repo>'

# 绕开当前被拒绝访问的 pytest 临时目录与缓存（见第 4 节环境注意事项）。
$env:PYTEST_DEBUG_TEMPROOT = "$PWD\.pytest-tmproot"
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --cov=mini_claude_code --cov-report=term

.\.venv\Scripts\ruff.exe check src tests examples
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe examples/cache_replay.py
.\.venv\Scripts\python.exe -m mini_claude_code.usage_report
.\.venv\Scripts\python.exe -m pip wheel --no-deps . --wheel-dir dist
```

ACL 恢复正常后，`pytest` 无需上面两个参数。wheel 构建可能需要联网获取构建依赖。MCP 传输层测试会启动本地 Python 子进程并包含若干秒等待，整套测试约 30 秒。

真实配置下启动交互式 CLI 会触发付费模型调用，不要把它当无成本测试。现有离线测试使用测试配置和模型桩。

## 7. 对接手 AI 的交付要求

先核对实际代码与本交接的差异。需要保留的契约：

- 稳定前缀、首次入历史限长、工具调用/结果配对、压缩失败不破坏历史。
- 所有执行路径共用 `tool_executor`；后台线程永不读 stdin。
- 任务绑定自己的 `Session`；`SESSION_STATE` 只在持 `agent_lock` 时换绑。
- 状态文件原子写入；写入失败保留原文件。
- MCP 严格 ID 匹配、请求超时、进程退出统一失败。

每个并发/权限修复补有意义的回归测试，断言外部可观察行为，不为了数字增加镜像实现的测试（不要写「源码里包含某个字符串」这类断言）。

最终用中文报告：改了哪些文件、解决什么问题、测试实际结果、尚未解决的风险，以及哪些事项确实需要用户提供仓库地址或付费预算。不要把已有测试结果描述成新一轮验证，也不要用“缓存友好”代替真实费用证据。
