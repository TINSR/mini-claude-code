# Mini Claude Code

[中文](README.md) · [English](README.en.md) · [CI](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml)

这是我跟着 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 学习时写的一个终端 coding agent。给它一个任务，它会读文件、调用工具、修改代码，再运行命令检查结果。目前主要在 Windows + PowerShell 下开发和测试。

最初所有逻辑都写在一个 4500 多行的文件里。后来拆成了几个模块，补了会话、记忆、子 Agent、定时任务、Git Worktree 和 stdio MCP。拆分前的文件保留在 [`docs/legacy_main.py`](docs/legacy_main.py)，方便对照着看。

我把它当作一个能实际运行的学习项目。你可以拿小项目试用，也可以顺着源码看一个 coding agent 是怎么接起来的。

## 开始使用

需要 Python 3.11+ 和 PowerShell。使用 Worktree 功能还需要 Git。

```powershell
git clone https://github.com/TINSR/mini-claude-code.git
cd mini-claude-code
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

编辑 `.env`，填写 `ANTHROPIC_API_KEY`、`ANTHROPIC_BASE_URL` 和 `MODEL_ID`。直连 Anthropic 时保留示例中的地址；使用兼容接口时换成对应地址和模型。DeepSeek 的兼容地址是 `https://api.deepseek.com/anthropic`。

```powershell
.\.venv\Scripts\mycc.exe
```

配置读取自启动目录，已有环境变量优先。建议先在小项目里试：例如让它解释一个函数，或者修一个已经有失败测试的 bug。模型调用会使用你配置的 API 额度。`mycc --help` 和 `mycc --version` 不需要密钥。

如果要在其他项目里运行，使用这里的 `mycc.exe` 完整路径，并在那个项目目录放配置。`skills/` 和 `mcp_servers/` 是示例资源，wheel 没有打包它们，需要时也要复制过去。

## 日常使用

`/new` 新建会话，`/sessions` 查看会话，`/switch` 切换，`/history` 查看历史；完整用法在 `/help`。会话保存在 `.sessions/`，跨会话记忆保存在 `.memory/`。输入 `exit`、按 Ctrl+C 或结束输入都会保存当前会话并关闭 MCP 连接；如果 Cron 正在执行，会先等它结束。

写文件和需要审批的命令会先询问。队友和定时任务在后台运行，不能弹出审批，因此需要审批的操作会被拒绝；想让它们执行，需先在主对话中为同一条操作保存长期许可。不同工作区的许可分开计算。

这些审批是工具层的检查，PowerShell 仍然能执行本机命令，不能当作隔离沙箱。第一次试用请选一个便于恢复的小项目。

## 关于缓存和 token

这部分是我花时间最多的地方。原先每轮都会缩短旧工具结果，导致下一次请求的前缀变化。现在新结果先限长，大结果存文件；入历史后尽量不再修改，直到上下文预算需要触发摘要。

这样更容易复用缓存，但也会发送更多历史。项目里记录了一次 DeepSeek 合成请求对照：缓存命中占比约从 31% 到 82%，未命中输入从 25,459 降到 11,021 token，总提示词却从 36,979 增到 61,965 token。按这组输入计数计算，缓存单价低于未命中单价的约 36.6% 才更便宜。它还没有验证真实修代码任务的总费用和完成质量。

原始结果和条件见 [`docs/CACHE_POLICY.md`](docs/CACHE_POLICY.md)。想看自己一次运行的用量：

```powershell
.\.venv\Scripts\mycc-usage.exe
```

用量日志在 `.transcripts/usage_*.jsonl`，不含对话正文；会话、摘要快照、记忆和工具输出文件会保留工作内容。分享运行记录前要检查这些文件。

## 开发与测试

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check src tests examples
.\.venv\Scripts\python.exe examples/cache_replay.py
```

测试使用离线模型桩；MCP 测试会启动本地 Python 子进程。`cache_replay.py` 也不调用 API。带 `live` 的基准脚本会调用真实模型，运行前请先看脚本参数。

模块关系见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，当前验证范围和后续工作见 [`docs/RELEASE_READINESS.md`](docs/RELEASE_READINESS.md)。目前还缺一个完整的真实编码任务演示；压缩上下文会打断旧缓存前缀，记忆调用也会带来额外用量。

## 来源与许可

学习与代码来源： [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)。本项目沿用了其中的教学实现，并在此基础上继续修改。版权声明见 [MIT License](LICENSE)。
