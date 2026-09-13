# Mini Claude Code

[English](README.md) · [中文](README.zh.md)

从零实现的 Claude Code 风格 Coding Agent：不依赖现成 Agent 框架，用 Python 手写工具调用、上下文压缩、长期记忆、任务调度、多 Agent 协作、Git Worktree 隔离与 MCP 动态工具发现。

[![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6)](https://www.microsoft.com/windows)
[![CI](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml)

**Windows 上的学习与作品集项目**，不是 Anthropic 官方产品，也不是生产级沙箱。从 4583 行单文件拆分而来，原始快照与哈希见 [`docs/BASELINE.md`](docs/BASELINE.md)。学习来源是 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)。

## 它能做什么

你输入任务，Agent 调用 PowerShell、读写文件，直到给出结论。循环背后还有：

- 主 Agent、子 Agent、后台任务、队友和 Cron 共用同一个授权入口
- 压缩过长上下文时保持工具调用与结果配对
- 任务、记忆、会话、定时任务都落在可检查的本地文件
- 用 Git Worktree 给队友隔离工作区
- 通过 stdio JSON-RPC 连接 MCP，运行时发现工具

## 快速开始

要求：**Windows**、**Python 3.11+**、**PowerShell**。Git Worktree 还要求当前目录在 Git 仓库中。

在项目根目录执行（独立克隆就是本目录；若克隆的是上层教程仓库，先进入 `mini-claude-code/`）：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

不花密钥就能确认安装：

```powershell
mycc --help
mycc --version
```

不花 API 费用的离线结构检查：

```powershell
python examples/cache_replay.py
```

要对话真实模型，编辑 `.env` 后运行 `mycc` 或 `mini-claude-code`。已有环境变量优先于 `.env`。DeepSeek 的 Anthropic 兼容接口把 `ANTHROPIC_BASE_URL` 设为 `https://api.deepseek.com/anthropic`，`MODEL_ID` 填账号可用的模型。

Skills 和 MCP 示例在源码目录里，wheel 不携带。从其他工作目录运行时，把所需的 `skills/`、`mcp_servers/` 复制过去。

## 架构

```text
User
  │
  ▼
CLI / Agent Loop
  ├── Prompt + Skills + Memory
  ├── Context compaction + Recovery
  ├── Tool registry + Hooks + Security
  ├── Background tasks + Cron
  ├── Shared tasks + Worktrees
  ├── Teammate runtime + Message bus
  └── MCP client ── stdio JSON-RPC ── MCP Server
```

| 模块 | 主要职责 |
|---|---|
| `cli.py` | 入口、工具 Schema、Agent Loop |
| `tools/` | 文件系统与 PowerShell 工具 |
| `tool_executor.py` | 所有执行路径共用的授权与执行入口 |
| `security.py` | 黑名单、审批、MCP 注解和线程审批能力 |
| `atomic_io.py` | 本地状态文件的原子写入 |
| `context_manager.py` | 上下文裁剪、压缩、转录和大型输出持久化 |
| `model_gateway.py` | 稳定工具顺序、服务端缓存适配、预算和原始 usage 日志 |
| `usage_report.py` | 按模型和用途汇总用量，不猜测兼容接口的计费公式 |
| `memory_manager.py` | 长期记忆提取、检索、索引和整理 |
| `task_manager.py` | 持久化任务、依赖、认领和完成 |
| `worktree_manager.py` | Git Worktree 创建、保留和安全删除 |
| `background_tasks.py` | 后台线程执行和完成通知 |
| `cron_scheduler.py` | Cron 校验、持久化和按归属会话交付 |
| `team_runtime.py` | 多 Agent 邮箱、生命周期与请求协议 |
| `mcp_client.py` | MCP 生命周期、超时、响应配对和动态工具池 |
| `session_manager.py` | 会话持久化、原子保存、切换和历史渲染 |

更完整的设计说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

### 会话命令

```text
/new [标题]       新建并切换会话
/session          查看当前会话
/sessions         查看所有会话
/switch <会话ID>  切换会话并显示历史
/rename <标题>    重命名当前会话
/history          重新显示当前会话历史
/delete <会话ID>  删除非当前会话
/help             查看命令帮助
```

会话保存在 `.sessions/`，长期记忆由 `.memory/` 跨会话共享。

## 缓存证据（可选）

`python examples/cache_replay.py` 比较旧版滚动改写历史与新版只追加历史：相同的 10 次合成请求中，完整保留上一次消息前缀的相邻请求分别是 **2/9 和 9/9**。这是结构验证，**不是**服务端缓存命中率。新方案累计发送字符更多：119,326 对 228,330。

把这套请求原样发给真实服务商（`python examples/cache_live_benchmark.py`，DeepSeek `deepseek-v4-flash`，10 轮/组）后：服务端报告的缓存命中占比 **31.1% → 82.2%**，未命中输入 token **25,459 → 11,021**，提示词总量 **36,979 → 61,965**。只有缓存命中单价低于未命中单价的 **36.6%** 时，新策略才更便宜（1/10 折扣约 0.61 倍，1/2 折扣反而贵 1.17 倍）。单一服务商、合成重复文本、关闭 thinking、`max_tokens=32`，**没有比较任务完成质量**。

完整数据见 [离线回放](docs/cache_replay_result.json) 与 [真实对照](docs/cache_live_result.json)，方法与边界见 [缓存说明](docs/CACHE_POLICY.md)。

```powershell
python -m mini_claude_code.usage_report
```

日志在工作目录 `.transcripts/usage_*.jsonl`，只保存元数据和原始 usage。压缩转录和工具结果文件含工作内容，不应公开。

## 测试

```powershell
python -m pytest
python -m compileall -q src
python -m ruff check src tests examples
python examples/cache_replay.py
```

当前离线测试覆盖文件安全、Hooks、权限、上下文、记忆、任务依赖、Worktree 删除保护、后台任务、Cron、团队消息和 MCP。此外还覆盖：同一个被拒绝的写入在主 Agent / 子 Agent / 队友三条路径都不执行；执行中切换会话结果仍存回原会话；保存中断后原会话文件仍可读；以及用 `tests/fake_mcp_server.py` 验证 MCP 的初始化顺序、通知与乱序响应、请求超时、进程退出和并发调用。

测试不会调用真实模型 API，也不会创建或删除真实 Git Worktree。MCP 传输层测试会启动本地 Python 子进程，包含若干秒的等待。

## 设计取舍

1. **不使用 LangChain 等 Agent 框架**：直接观察模型 API 消息、工具调用与状态机的真实形态。
2. **保留教学实现**：模块拆分以移动原函数为主，避免为了“看起来高级”而改写掉学习过程。
3. **显式状态注入**：线程、提示词、MCP 和多 Agent 模块不再依赖隐式跨文件全局变量。
4. **默认保护用户数据**：路径限制在工作目录内；危险命令直接拒绝；存在未保存改动时拒绝删除 Worktree。
5. **本地持久化优先**：任务、记忆、邮箱、转录和 Cron 状态都使用可检查的本地文件。

## 项目边界

PowerShell、文件写入、网络搜索和多 Agent 并发都需要在可信工作目录中使用。

### 队友的权限取舍

队友、后台任务和 Cron 都运行在后台线程，不能弹出审批。它们遇到需要人工审批的操作时**直接被拒绝**，并在工具结果里说明原因：

- 队友可以自由使用 `read_file`、`glob` 和任务协作工具。
- 队友的 `write_file` 只有在你已经在主 Agent 里为**完全相同的操作**选择过「以后允许」时才执行。
- 队友的 PowerShell 只能执行权限策略本来就自动放行的命令，命中危险关键词的一律拒绝。

这是有意的功能取舍：同一个被拒绝的操作在任何路径都不能执行。

### 已知限制

- PowerShell 的审批规则是关键词匹配，不是沙箱。
- 长期许可按「工作区 + 工具名 + 完整参数」指纹匹配，参数变一个字符就会重新询问。
- 上面的缓存数字只覆盖一个服务商、一个模型、一段合成重复文本；没有真实任务的完成质量基准，也没有把输出、摘要和记忆调用计入的总账单对照。压缩会打断前缀，长会话不会一直保持 82.2% 命中。
- Cron 一轮正在执行时，会话命令会等它结束才生效。
- 全新 Windows 环境从零安装、Skills/MCP 资源随包分发都还没验证。

权限模型见 [SECURITY.md](SECURITY.md)。

## 目录

```text
.
├── src/mini_claude_code/   正式 Python 包
├── tests/                  离线回归测试
├── examples/               缓存回放（免费）与真实对照（付费）
├── skills/                 示例 Skills（不在 wheel 里）
├── mcp_servers/            示例 MCP Server（不在 wheel 里）
├── scripts/                历史拆分脚本，不要重新执行
├── docs/                   架构、原始实现基线、缓存数据
└── .github/workflows/      GitHub Actions（本目录作为仓库根时才会跑）
```

## License

[MIT](LICENSE)
