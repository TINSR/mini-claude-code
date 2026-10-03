# AI 交接（更新：2026-10-03）

## 项目与目标

用户希望把自己学习编写的 coding agent 做成适合 GitHub 个人主页展示的项目，关心缓存命中与总费用，并要求 README 像个人作者写的，简洁、具体，不堆宣传词。

活跃版本是本仓库 `src/mini_claude_code/`。父目录 `my_claude_code/main.py` 是原始单文件版本；`docs/legacy_main.py` 是不可改写的历史快照，测试校验哈希。历史 `scripts/extract_*.py` 不能作为升级脚本重新运行。

本目录已是独立 Git 仓库，origin 为 `https://github.com/TINSR/mini-claude-code.git`。先查看 git status，保留用户已有修改。仓库地址已知，不需要再次要求用户提供。当前工作的授权是本地改进；是否提交、推送或发布，按用户后续指示执行。

## 已完成

- append-only 历史、稳定 system 与工具顺序、首次写入前限长并存完整工具输出、上下文预算及失败不破坏历史的摘要。
- 统一工具授权入口；后台需要人工审批的操作直接拒绝，不占用 stdin。
- Cron 绑定所属会话、会话操作互斥、状态 JSON 原子替换、交付失败有限重试。
- MCP 初始化顺序、读取线程按 ID 分发、超时与进程退出处理、并发写入保护。
- Windows Shell 字节捕获与解码、文件分页、usage 记录和 CLI 无密钥 help/version。
- 本轮补 CLI 的正常退出 / Ctrl+C / EOF / 异常 finally 清理；停止 Cron 轮询、保存当前会话、关闭 MCP/API。已开始的 Cron 轮次会先结束，尚无强制取消机制。
- 更新 README 中英文、发布检查、CHANGELOG、上游 MIT 声明、项目 URL、Issue/PR 模板和 CI 打包检查。

2026-10-03：158 项离线测试通过，Ruff 和 compileall 通过。打包安装的最新细节以 [RELEASE_READINESS.md](RELEASE_READINESS.md) 为准。本轮未重新测覆盖率；历史约 80% 不要写成新结果。

## 缓存结论与后续任务

已有真实服务端**合成请求**记录：命中占比约 31% → 82%，总输入也增大。详情见 [CACHE_POLICY.md](CACHE_POLICY.md)。本轮没有付费调用，也没有重新复现该历史记录。

真实编码任务的质量与总费用基准仍缺。需要固定项目、任务、模型和冷/热缓存条件，计入输出、摘要、记忆与完成质量；在用户明确预算后运行。不能把结构指标当服务端命中，不能从合成请求推导通用省钱承诺。

接下来的重点是：

1. 核对将要发布的提交在远程 CI 上通过。
2. 真实小任务演示、去敏 diff / 测试 / usage 和终端截图。
3. 队友完整生命周期、真实临时 Git Worktree 的端到端测试。
4. 根据 usage 定位额外记忆调用和续写费用，再决定优化。

## 实施约束与检查

保留稳定前缀、工具配对、事务式压缩、统一授权、工作区许可隔离、会话归属、原子保存和 MCP ID 配对等契约。优先写验证外部行为的回归测试。

普通本地修复无需反复确认。不要输出密钥或将 .env、会话、记忆、审批、转录、本地审计产物提交。上下文快照和工具输出包含实际工作内容，usage 去除正文不能代替这些文件的去敏。

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .audit/handoff-tests
.\.venv\Scripts\python.exe -m ruff check src tests examples
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m build --outdir dist
```

交付时用中文说明文件改动、实际验证和剩余限制；不要重复把已解决问题列为待修复，也不要把旧测试结果写成刚验证的结果。
