# GitHub 展示与发布检查（更新：2026-09-13）

## 判断

按“个人 GitHub 主页置顶、让访客能理解并试用”的标准检查。当前已有足够的教学内容和模块化实现，可以作为明确标注实验性质的公开项目展示。GitHub 首页推荐或 Trending 是外部结果，不能靠代码修改保证。

2026-09-12 完成缓存算法与对应测试、文档和安装入口改进。2026-09-13 修掉了此前列为 P0/P1 的三个并发与权限缺口（见下方“2026-09-13 完成”）。剩余缺口是**真实任务与费用基准**和**发布准备**，这两项都需要用户提供付费预算或仓库地址。

## 2026-09-13 完成

- 统一授权入口：新增 `tool_executor.py`，主 Agent、子 Agent、后台任务和队友都经过同一组 PreToolUse / PostToolUse Hook 与权限策略。
- 审批能力按线程声明：`security.set_execution_context()` 是 thread-local。后台线程遇到 `ask` 直接拒绝并说明，不从后台读 stdin，因此不存在后台等审批造成的死锁。功能取舍已写入 README。
- 许可指纹按工作区区分：队友 Worktree 与主工作区的同名路径是不同的长期许可；文件权限检查也按调用方的 `base_dir` 解析真实目标。
- 会话归属：任务从开始就绑定自己的 `Session` 对象；`SESSION_STATE` 只在持 `agent_lock` 时换绑；会话命令移入锁内。执行中切换会话，结果仍存回原会话且不抢 `current` 指针。
- Cron 归属：`CronJob.session_id` 记录创建会话，到期按归属会话分组交付；归属会话被删除则跳过。旧的持久化文件缺少该字段时按空值加载。
- 原子写入：新增 `atomic_io.py`，`.sessions/*.json`、`.scheduled_tasks.json` 和 `approvals.json` 先写同目录临时文件再 `os.replace`。写入中断保留原文件，也不留临时文件。
- MCP 传输层：生命周期改为 `initialize` → `notifications/initialized` → `tools/list`；读取线程按 id 分发响应，丢弃通知和无人等待的 id；请求带超时；进程退出统一失败挂起请求；stdin 写入加锁支持并发调用；握手失败不留孤儿进程，CLI 退出关闭全部连接。
- 新增 `tests/fake_mcp_server.py`，用真实子进程覆盖通知、乱序响应、超时、进程退出和并发调用。

经一轮代码审查后补的修复：

- Cron 交付线程加异常兜底。此前 `queue_processor_loop` 没有 `except`，一次磁盘错误就会让它静默结束、之后所有定时任务不再交付，而且已出队的那批任务被丢弃。现在拆出可单独调用的 `process_cron_batch()`，失败的批次退回队列，同一任务连续 3 次失败才放弃。取舍：交付变成至少一次而非恰好一次，且重试无退避。
- 轮次内新建的 Cron 任务归属**正在运行的**会话（`turn_session()` thread-local），而不是界面上的当前会话。
- 子进程不再继承终端 stdin：`run_powershell` 和 Git 调用都传 `stdin=subprocess.DEVNULL`。此前后台线程里等待输入的命令会抢走用户按键或阻塞到超时，与“后台线程不读 stdin”的说法矛盾。
- `.scheduled_tasks.json` 的快照与写入都在 `cron_lock` 内，避免迭代中断和用旧快照覆盖新任务。
- 队友空闲自动认领任务改为经过统一授权入口（`tool_executor.ToolCall`）。这是此前唯一绕过入口的队友工具调用。

## 2026-09-12 完成

- 普通轮次不再改写旧工具结果；稳定 system，工具按名称排序。
- 新结果首次入历史前限长/落盘，文件读取支持分页；后台结果通知也限长。
- 大结果文件和压缩快照按所属工作区保存，队友 Worktree 可以读取自己的引用。
- 主 Agent、子 Agent、队友都有本地上下文预算；默认 32,000 估算 token，包含输出预留。
- 摘要失败、截断、为空或没有缩短历史时保留原上下文；正常压缩保留近期工具配对。
- 原始 usage、调用目的、前缀变化统一记录；提供 `mycc-usage` 汇总。
- 适配直连 Anthropic 的显式缓存；DeepSeek 保持自动缓存；未知兼容接口不擅自加参数。
- 同步修复 PowerShell GBK/UTF-8 解码问题。
- CLI 延迟创建 API 客户端，`--help` / `--version` 无需密钥；配置从工作目录读取，环境变量优先。
- 补充 README、缓存文档、离线回放及贡献者检查命令；原始快照哈希保持不变。
- 修正 `.gitignore`：遗漏的长期审批、临时构建、测试目录和 `.env` 变体已排除。
- CI 增加 lint、离线回放、wheel 构建及从工作区外验证安装入口的步骤。

## 验证证据（2026-09-13 重新运行）

本地环境为 Windows、Python 3.13.12。

| 项目 | 结果 | 边界 |
| --- | --- | --- |
| pytest | 153 项通过（2026-09-12 为 114 项） | 离线测试，没有真实模型调用 |
| Ruff | src/tests/examples 全部通过 | 代码静态检查 |
| compileall | 通过 | 源码编译 |
| 总语句覆盖率 | 80%（2026-09-12 为 72.57%） | 不能替代端到端与真实并发压力测试 |
| tool_executor / atomic_io | 100% | 模块很小，高覆盖不代表策略本身完备 |
| session_manager | 95%（原 —） | 覆盖原子保存中断和非当前会话保存 |
| cron_scheduler | 82%（原 —） | 覆盖会话归属、交付失败重试、并发保存 |
| mcp_client | 87%（原 —） | 覆盖超时、乱序、进程退出、并发 |
| security | 90% | 覆盖非交互拒绝和 Worktree 指纹隔离 |
| context_manager / model_gateway | 约 88% / 91% | 与 2026-09-12 持平 |
| CLI / team_runtime | 63% / 63%（原 55% / 43%） | 仍是后续测试重点 |
| 离线缓存回放 | 与 2026-09-12 完全一致：2/9 对 9/9，119,326 对 228,330 字符 | 结构指标，不是服务端命中率 |
| 真实缓存对照（新增） | DeepSeek `deepseek-v4-flash`，命中率 31.1% → 82.2%，未命中 token 25,459 → 11,021，提示词总量 36,979 → 61,965 | 单一服务商模型、合成重复文本、thinking 关闭、未测输出与任务质量 |
| wheel | 构建成功，29 个归档条目（新增两个模块） | 未做 sdist 发布验证 |
| wheel 目标目录安装 | 成功；`--version`、`--help`、`mycc-usage` 无密钥可用；缺配置退出码 2 | 依赖复用现有 venv，并非全新系统 |
| 打包内容检查 | 只含 24 个源码模块和 dist-info；没有 `.env`、会话、记忆、转录和审批文件 | wheel 不包含示例 Skills/MCP 资源 |
| 候选公开文件扫描 | 2026-09-12 的结果，本次未重跑 | 有限规则扫描，不等于完整秘密审计 |

本地环境注意事项：若 `%LOCALAPPDATA%\Temp\pytest-of-*` 或仓库内 `.pytest_cache` 的 ACL 拒绝访问，直接 `pytest` 会在 fixture 阶段报 `PermissionError: [WinError 5]`。这是环境问题，不是代码问题。绕开方式见“本地验证命令”。

可复现命令（本地验证）：

```powershell
# 绕开当前被拒绝访问的 pytest 临时目录与缓存，见上方环境注意事项。
$env:PYTEST_DEBUG_TEMPROOT = "$PWD\.pytest-tmproot"
python -m pytest -p no:cacheprovider --cov=mini_claude_code
python -m ruff check src tests examples
python -m compileall -q src
python examples/cache_replay.py
python -m pip wheel --no-deps . --wheel-dir dist
```

ACL 恢复正常后，`python -m pytest` 无需上面两个参数。

离线回放中，旧策略 9 组相邻请求有 2 组完整保留前缀，新策略 9 组全部保留；累计序列化字符为 119,326 对 228,330。它只说明缓存复用条件改善，也显示了增加输入的代价。**没有真实服务端命中率或费用下降证据，不能宣传“命中率 100%”或“已节省 X%”。** 数据在 [cache_replay_result.json](cache_replay_result.json)，运行后如何验证真实 usage 见 [CACHE_POLICY.md](CACHE_POLICY.md)。

## 已关闭的遗留项

### P0：统一队友权限边界 —— 已完成（2026-09-13）

原依据：[team_runtime.py](../src/mini_claude_code/team_runtime.py) 的 `run()` 工具循环直接执行 `handler(**block.input)`，绕过主 Agent 的 PreToolUse / permission Hook。

现状：四条执行路径都经过 [tool_executor.py](../src/mini_claude_code/tool_executor.py)。回归测试 `tests/test_tool_authorization.py` 用真实的队友线程和真实的 `agent_loop` / `spawn_subagent` 驱动同一个被拒绝的写入，断言三条路径都没有调用处理器。

功能取舍：队友实质上是「只读 + 已保存长期许可」。README 已明确写出，没有宣称队友拥有完整可写权限。

### P1：会话切换、Cron 与保存的一致性 —— 已完成（2026-09-13）

原依据：`handle_session_command()` 在 `agent_lock` 之外；`run_session_agent()` 保存的是回调结束时的当前 session；`save_session()` 直接覆盖 JSON。

现状：任务绑定自己的 `Session`；会话命令移入 `agent_lock`；Cron 按 `CronJob.session_id` 交付；三个 JSON 状态文件改为原子替换。回归测试覆盖「执行中切换会话」「异常退出仍保存到原会话」「保存中断保留原文件且不留临时文件」「保存非当前会话不抢 current 指针」。

### P1：MCP 初始化、超时与响应配对 —— 已完成（2026-09-13）

原依据：先请求 `tools/list` 再发 `notifications/initialized`；`send_request()` 阻塞 `readline()`，无超时、无严格 ID 匹配、无并发保护。

现状：生命周期顺序改正；读取线程按 id 分发；超时、进程退出、并发写入都已处理。规范依据：[MCP 2025-06-18 lifecycle](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle)。测试用 `tests/fake_mcp_server.py` 断言**实际通信行为**：服务端回报它观察到的方法顺序、乱序响应各自归位、超时后迟到响应被丢弃、进程退出后请求立即失败。

## 公开完整功能前优先处理

### P1：真实模型演示与缓存成本基准

目前有离线回放，但缺少真实任务完成、代码 diff、验证结果与账单用量相互对应的案例。

完成条件：选择一个 1–2 分钟可理解的小任务，例如“定位失败测试并修复”，提供输入、工具调用摘要、最终 diff、测试结果和去敏 usage。固定任务/模型做多轮冷缓存与热缓存对照，分别报告输入命中、未命中、输出、摘要次数及完成质量。不要把测试桩结果当服务端缓存数据。

## 让 GitHub 首页更有说服力

| 优先级 | 还需要什么 | 可交付成果 |
| --- | --- | --- |
| P1 | 独立仓库及真正的远程地址 | 当前整个目录在父仓库仍是未跟踪内容，父仓库 origin 是 `shareAI-lab/learn-claude-code`。确定自己的目标仓库再发布；不要直接推到当前上游 origin |
| P1 | CI 真正在公开仓库运行 | 当前工作流位于子目录 `.github/workflows`，不会成为父仓库的根级工作流；独立仓库后可直接使用，保留父仓库则需放在根级并设置工作目录 |
| P1 | 一眼看懂的首页演示 | 一张去敏终端图或短 GIF，展示“任务 → 调工具 → 修改 → 测试通过 → 用量”；补实际结果，不只是功能清单 |
| P1 | 安装后的资源体验 | wheel 未携带 Skills/MCP 示例。已文档化手动复制；后续可增加资源包或 `mycc init`。在全新 Windows 环境验证从零安装 |
| P2 | 端到端和并发覆盖 | Cron 会话绑定和同一 MCP 连接并发已覆盖。仍缺：Ctrl+C / EOF、失败输出恢复、队友 idle_poll 与关机协议的完整生命周期、真实 Git 临时仓库测试 |
| P2 | 仓库信息与来源 | 填写真实 Repository/About/Topics/项目 URL；核对学习来源、实际沿用代码与对应版权说明，目前独立 LICENSE 仅署名 TIM |
| P2 | 维护入口 | Issue/PR 模板、CHANGELOG、版本发布说明、已知限制列表；按受众补简短英文介绍 |
| P2 | 简化主循环 | cli.py 仍混合工具 schema、运行时接线和交互；可继续拆出注册表/运行时对象，避免跨模块全局状态 |
| P2 | 后续费用优化 | 记忆选择/提取仍调用模型；输出截断仍可能重生成。先用新增 usage 定位占比，再单独优化 |

## 建议交付顺序

1. ~~先修队友授权、Cron 会话隔离及 MCP 稳健性~~ —— 2026-09-13 完成，队友的权限取舍已写入 README。
2. 准备真实小任务演示及去敏用量，形成可复现案例。**需要用户先指定服务商、模型和可用预算。**
3. 确定独立仓库，检查公开文件、来源说明和资源安装，然后让 CI 在该仓库实际跑通。**需要用户先提供目标仓库地址。**
4. 发布一个诚实描述能力与限制的版本，置顶到个人主页。

2026-09-12 和 2026-09-13 两次都只修改本地项目、执行离线测试和打包验证，没有创建远程仓库、提交或推送代码，也没有使用模型 API 余额。构建所需依赖已通过批准的联网构建下载。
