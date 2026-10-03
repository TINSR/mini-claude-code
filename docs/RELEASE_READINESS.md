# 发布检查（2026-10-03）

## 当前结论

当前版本适合以实验性学习项目发布 0.1.0。统一工具授权、Cron 会话归属、原子保存、MCP 响应配对和退出清理已有实现及回归测试。完整真实编码任务的质量与总费用对照仍未完成，不能据此称为稳定产品或宣称普遍节省费用。

本目录已经是独立 Git 仓库，origin 为 `https://github.com/TINSR/mini-claude-code.git`，CI 位于仓库根级 `.github/workflows/ci.yml`。此前文档中“整个目录未跟踪”“需要提供仓库地址”“子目录 CI 不生效”的判断已过时。远程工作流是否成功，需以 GitHub 上对应提交的运行结果为准。

## 本轮改进

- CLI 用统一 finally 清理正常退出、Ctrl+C、EOF 和运行异常：保存当前会话，停止 Cron 轮询，关闭 MCP 与 API 客户端。
- 保存失败会报告错误，仍继续关闭资源。Cron 已经开始的轮次先结束；目前没有强制取消正在进行的模型或工具调用。
- 增加 5 个退出回归场景，验证历史落盘、后台循环结束和资源关闭。
- 更新中英文 README、交接、CHANGELOG，保留上游 shareAI Lab 的 MIT 版权声明。
- 补充包的 Repository / Issues 元数据与 Issue / PR 模板。
- CI 构建源码包再从源码包构建 wheel；PowerShell 安装冒烟逐步检查退出码。
- 源码包使用显式目录清单，避免将运行状态和本地审计目录收入发行包。wheel 仍只包含 Python 包；示例资源需手动复制。

## 验证结果

本轮在 Windows、Python 3.13.12 上重新运行：

| 检查 | 结果与范围 |
| --- | --- |
| pytest | 158 项通过；离线模型桩及本地 MCP 子进程 |
| Ruff | src/tests/examples 全部通过 |
| compileall | 通过 |
| 本轮新增退出测试 | 正常退出、EOF、Ctrl+C、模型异常、保存异常均覆盖 |
| 打包 | 源码包构建成功，从源码包构建 wheel 成功；wheel 29 条目、源码包 69 条目 |
| 从零安装 | 新建独立 venv，重新安装全部依赖，pip check 通过；未复用开发环境依赖 |
| 项目外安装冒烟 | 7 项通过：安装路径、help、version、usage、缺配置退出 2、正常退出、EOF 退出；使用占位配置，无模型请求 |
| 打包内容与链接 | 未收入私人运行目录或真实 .env；本轮编辑文档的相对链接均有效 |
| 有限敏感信息扫描 | 81 个候选文本文件未命中字面密钥与私钥模式；不等于完整秘密审计 |

2026-09-13 的历史覆盖率约 80%，本轮未重新测量覆盖率。测试通过不等于真实任务质量或完整并发压力测试已经完成。

本地安装冒烟记录在 `.audit/release-smoke-20261003.json`。只验证了当前 Windows / Python 3.13.12，不等于 CI 中 Python 3.11、3.12 的矩阵已通过。

离线回放仍是旧策略 2/9、新策略 9/9 保留完整相邻前缀，累计字符 119,326 对 228,330。这是结构指标。

项目另有 2026-09-13 的真实服务端合成请求记录：DeepSeek 上命中占比 31.1% → 82.2%，未命中输入 25,459 → 11,021，总提示词 36,979 → 61,965。记录见 [CACHE_POLICY.md](CACHE_POLICY.md) 与 [cache_live_result.json](cache_live_result.json)。本轮未重新调用付费模型，也未独立复现该历史测量。

这组输入计数只有在缓存单价低于未命中单价约 36.6% 时才降低输入费用；完整任务还需计入输出、记忆、摘要和任务是否完成。不要将合成请求结果推广为实际编码任务的固定命中率或省钱比例。

## 还需要完成

1. 发布前确认远程 CI 在将要发布的提交上通过。本轮修改尚未提交或推送。
2. 准备一个真实任务示例：输入、最终 diff、测试结果和去敏 usage，最好配一张终端图。真实模型运行需明确可用预算。
3. 后续补队友退出协议与真实 Git Worktree 的端到端测试；继续分析记忆调用和输出重生成的费用。
4. 发布前检查候选公开文件，排除 .env、会话、记忆、转录、审批和私人任务内容。关键词权限检查不能称为系统沙箱。

## 复现命令

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .audit/release-tests
.\.venv\Scripts\python.exe -m ruff check src tests examples
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe examples/cache_replay.py
.\.venv\Scripts\python.exe -m build --outdir dist
```

`--basetemp` 会清理指定的临时目录，请只指向专用测试目录。打包可能联网安装构建依赖。真实 API 基准脚本与上述离线测试不同，会使用模型额度。
