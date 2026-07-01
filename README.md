# MemoryHub

持久、高保真、可生长的**本地个人记忆系统**。把你散落在各家 AI 里的对话与记忆,沉淀成一座
**带逐字证据、可溯源、你说了算**的私人记忆库,再通过 MCP 让任何 AI 随时召回。

- 🔒 **数据不出本机** —— 全部跑在 `127.0.0.1`,记忆库是本地 SQLite;唯一的联网是可选的「检查更新」(只拉版本号)。
- 🧾 **保真优先** —— 每条记忆必须带**原文逐字证据**,无证据不入库(硬防幻觉);AI 推断来的一律只提名、你确认才入库。
- 🌱 **多源可生长** —— Claude Code / Claude / ChatGPT 的导出都能接;写自己的连接器也只是照葫芦画瓢。
- 🧩 **随取随用** —— 零依赖 MCP 服务器暴露 `recall_memory`,Claude Code / Claude 桌面端即插即用。

> 本仓库仅含**代码 / schema / 脚本**,**不含任何个人记忆数据**。你的数据只存在你自己机器上。

## 支持的数据源

| 平台 | 通道 | 导入 | 召回 / 使用 | 状态 |
|---|---|---|---|---|
| **Claude Code**(本地) | 本地 transcript 直读 | `scripts/ingest.py` | MCP `recall_memory` | ✅ 已实现 |
| **Claude(网页 / 桌面)** | 账号级官方导出(`conversations.json`) | `scripts/ingest_claude_web.py` | MCP `recall_memory` | ✅ 已实现 |
| **Claude 云端记忆** | 导出内 `memories.json` | `scripts/ingest_claude_memories.py` · **面板一键导入** | 人工确认后入库 | ✅ 已实现 |
| **ChatGPT(网页 / 桌面)** | 账号级官方导出(`conversations.json`) | `scripts/ingest_chatgpt.py` | 主动读取指令 | ✅ 已实现 |
| Codex | 本地会话日志 | — | 主动读取指令 | 🚧 计划中 |
| 各端本地历史直读(免导出) | 本地缓存解析 | — | — | 🚧 计划中 |
| 自动增量同步 | 免手动导出 | — | — | 🚧 计划中 |

> 桌面端与网页端用的是**同一份账号级官方导出**,因此复用同一个连接器。

## 快速开始
需要 Python 3.11+。
```bash
git clone https://github.com/Shiye-10Pages/MemoryHub.git
cd MemoryHub
scripts/setup.sh          # 建虚拟环境、装依赖、初始化空库
scripts/run_web.sh        # 启动本地面板 → http://127.0.0.1:7788
```
首次打开面板,顶部会给你三步引导:① 填 `ALIBABA_KEY` → ② 导入你的 AI 记忆 → ③ 在「待确认」里逐条批准。

`.env` 里的 `ALIBABA_KEY`(阿里云百炼 DashScope)用于提纯 / 向量化 / 语义召回;**不填也能用**浏览 + 关键词检索。
`MINIMAX_KEY`、`PERSONA_NAME` 均为可选(见 `.env.example` 注释)。

## 一键导入 Claude 记忆
1. 打开 Claude → **Settings → Privacy → Export data**,稍等收到导出邮件,下载并解压 zip。
2. 找到里面的 `memories.json`(Claude 对你的 AI 记忆)。
3. 在面板 **「导入」** 页,把 `memories.json` 拖进去(或点选文件)。
4. 系统把它拆成候选、过四道保真闸,落到 **「待确认队列」**;你逐条**批准 / 丢弃**——AI 推断的记忆绝不自动入库。

> ChatGPT / claude.ai 网页对话:同样是账号级导出里的 `conversations.json`,放进 `imports/` 后跑对应的 `scripts/ingest_*.py`。

## 让其他 AI 主动读取你的记忆
面板 **「设置 → 接入 AI」** 会给你复制即用的:
- **Claude Desktop / Claude Code 的 MCP 配置**(已自动填好本机绝对路径);
- 一段 **「主动读取」指令**,贴进 Claude Project instructions / ChatGPT 自定义指令 / 其他 AI 的系统提示,
  让它在回答涉及事实 / 既定决策 / 偏好前,先查 MemoryHub 并以其逐字证据为准。

也可手动注册 MCP:
```bash
claude mcp add memoryhub -- /path/to/.venv/bin/python /path/to/MemoryHub/scripts/mcp_server.py
```
生效后任意会话即可调用 `recall_memory`。更多见 `docs/接入其他AI.md`。

## 自动检查更新
面板每天一次向 GitHub 查是否有新版本(**只拉版本号,无任何遥测**);有新版会在「设置」里提示。离线则静默跳过。

## 扩展
想接自己的数据源?见 `docs/extending.md` 与 `examples/ingest_custom_jsonl.py`;保真契约见 `CONTRACT.md`。

## 许可证
本项目采用 **PolyForm Strict License 1.0.0**(全文见 [`LICENSE.md`](LICENSE.md))——**源码可见(source-available),不是标准开源**:

- ✅ 个人 / 非商业**免费使用**;
- 🚫 **禁止商业使用**;
- 🚫 **禁止二次开发分发**(不得分发本软件,也不得分发基于它的修改 / 衍生作品)。

---

**十页 AI 出品** · 官网:<https://shiyeai.cn>
