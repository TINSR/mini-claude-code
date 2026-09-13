# Mini Claude Code

[中文](README.md) · [English](README.en.md)

[![CI](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml/badge.svg)](https://github.com/TINSR/mini-claude-code/actions/workflows/ci.yml)

我跟着 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) 把 Claude Code 这类东西自己写了一遍。没用 LangChain。Windows + PowerShell，能读文件、改文件、跑命令，也会自己决定下一步干什么。

最初是一个 4500 多行的单文件，后来拆开了。旧文件还在 [`docs/legacy_main.py`](docs/legacy_main.py)，没改过。这不是官方产品，也别当沙箱用。

## 怎么跑

Python 3.11+，在仓库根目录：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

先改 `.env` 里的 key，再 `mycc`。`mycc --help` 不需要 key。

DeepSeek 的话，`ANTHROPIC_BASE_URL` 填 `https://api.deepseek.com/anthropic`，`MODEL_ID` 填你账号里能用的那个。

会话命令就几条：`/new`、`/sessions`、`/switch`、`/history`。历史在 `.sessions/`，跨会话还想留下的东西在 `.memory/`。

## 里面有什么

主循环就是普通的 messages + tool_use。写文件和危险命令会问你。子 Agent、后台任务、队友、定时任务走同一套权限——后台线程不能弹窗，该问的就直接拒，免得卡住。

上下文默认只往后面加，旧结果不回头改，好让请求前缀稳一点。太大了会摘要，原对话扔到 `.transcripts/`。记忆是目录里的 Markdown，需要的时候贴进这一轮用户消息，不塞进 system。

队友可以放到 Git Worktree 里。MCP 用 stdio 接。`skills/` 和 `mcp_servers/` 是示例，打出来的 wheel 里没有，换目录跑的话自己拷过去。

结构说明在 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

## 缓存那点事

我比较在意「前缀稳了会不会其实更费钱」。离线回放（`python examples/cache_replay.py`，不花 API）里，相邻请求前缀从 2/9 变成 9/9，发出去的字也差不多翻倍。

同一套请求后来在 DeepSeek 上跑过：命中大概 31% 到 82%，没命中的 token 少了一半多，但总量更大。命中价不到未命中价的四成，这套才更便宜；折扣浅了反而亏。一次合成文本，别当通用结论。数字在 [`docs/CACHE_POLICY.md`](docs/CACHE_POLICY.md)。

用量：`python -m mini_claude_code.usage_report`。日志在 `.transcripts/usage_*.jsonl`，没有对话正文。

## 测试

```powershell
python -m pytest
python -m ruff check src tests examples
```

不打真实模型，也不动你机器上的 Git worktree。MCP 那几个测试会起本地 Python 进程，大概半分钟。

## 别指望的

PowerShell 拦的是关键词，不是沙箱。队友默认几乎只能读；要写文件，得你先在主对话里对**同一条操作**点过「以后允许」。长会话压过一次上下文，缓存就断了。定时任务跑着的时候，切会话得等。

## License

[MIT](LICENSE)
