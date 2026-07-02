# AI 记忆助手 · MemoryHub

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
| **Codex CLI**(本地) | 本地会话直读(`~/.codex/sessions`) | `scripts/ingest_codex.py` · **面板一键扫描** | 主动读取指令 | ✅ 已实现 |
| 桌面端缓存直读(Claude / ChatGPT) | 本地缓存解析 | — | — | ❌ 不做:ChatGPT 桌面端对话本地加密(Keychain 持钥),Claude 桌面端为非稳定 LevelDB 且对话在云端——不破解应用加密,请用上面的**官方导出**通道 |
| **自动增量同步**(本地源) | 面板后台定时 / 一键 | 「导入」页 ⟳ 立即同步 · 每日自动(可开关) | 增量进待确认队列 | ✅ 已实现 |

> 桌面端与网页端用的是**同一份账号级官方导出**,因此复用同一个连接器。

## 快速开始
需要 Python 3.11+。
```bash
git clone https://github.com/Shiye-10Pages/MemoryHub.git
cd MemoryHub
scripts/setup.sh          # 建虚拟环境、装依赖、初始化空库
scripts/run_web.sh        # 启动本地面板 → http://127.0.0.1:7788
```
之后**双击启动**即可:macOS `打开记忆面板.command`、Windows `打开记忆面板.bat`(首次会自动初始化)。首次打开面板顶部会给你三步引导:① 配 API Key → ② 导入记忆 → ③ 在「待确认」里逐条批准。

> macOS 首次双击若被拦(“来自身份不明的开发者”),**右键 → 打开**一次即可;或到 系统设置 → 隐私与安全性 →“仍要打开”。
> Windows 的 `打开记忆面板.bat` 尚未在真机验证,遇到问题请提 Issue。
> 想要**无终端窗口 + 品牌图标**的 App:`osacompile -o MemoryHub.app -e 'do shell script "bash \"'"$PWD"'/打开记忆面板.command\""'`,再在其“显示简介(⌘I)”里把 `assets/icon-dark.png` 拖到左上角图标上。品牌图标(深/浅两版 + `MemoryHub.icns`)在 `assets/`,可用 `python3 assets/make_icon.py` 重新生成。

### 配 API Key(在面板里,不用改文件)
打开 **「设置 → AI 模型 · API Key」**:选一个 provider、粘 key、点**测试连通**。支持 **阿里云百炼(默认)/ OpenAI / DeepSeek / 智谱GLM / Kimi / SiliconFlow**,或任意 OpenAI 兼容服务。**导入记忆、提纯、语义召回都需要 key**(把记忆向量化);不配 key 只能浏览**已有**记忆 + 关键词检索,无法导入新记忆。
- 推荐:提纯用强指令模型(qwen3-max / deepseek-chat / gpt-4.1-mini / glm-4),嵌入用 text-embedding-v4 / text-embedding-3-small / bge-m3。
- 注意:DeepSeek、Kimi 不提供嵌入,需在 `.env` 另配一家嵌入(如 openai / dashscope)。`PERSONA_NAME` 可选(给提纯 prompt 署名)。

## 导入记忆
面板 **「导入」** 页两种方式:
- **本机 Claude Code(免导出)**:点「🔍 扫描本机 Claude Code 对话」,直读 `~/.claude/projects`。
- **拖入导出**:Claude(Settings → Privacy → Export data)或 ChatGPT(Settings → Data controls → Export)的导出,**整个 zip 直接拖进去**(自动识别 `memories.json` / `conversations.json`),也可拖单个 json。

拆成候选 → 过四道保真闸 → 落「待确认队列」,你逐条**批准 / 丢弃**;AI 推断的记忆**绝不自动入库**。

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

## 自动检查更新 + 一键更新
面板每天一次向 GitHub 查是否有新版本(**只拉版本号,无任何遥测**);有新版会在「设置」里红字提示 + 顶部小红点。离线则静默跳过。

发现新版时点**「立即更新」**即可:面板自己 `git pull` 拉新代码、按需补装依赖、然后**自动重启**生效——全程不碰你的本地数据(记忆库、`.env` 均在 `.gitignore` 里,`git pull` 不会动)。若你本地改过代码(未提交)或不是 `git clone` 装的,会中止并提示手动更新,绝不覆盖你的改动。

## 数据备份
你的全部记忆只存在本机 `memory.db`,**不在任何云端**——备份靠你自己。面板 **「设置 → 数据 · 备份」** 一键下载 zip(内含一致性快照的 `memory.db` + 版本号);恢复时用包里的 `memory.db` 替换安装目录同名文件、重启面板即可。建议每次大批量导入后备份一次。

## 扩展
想接自己的数据源?见 `docs/extending.md` 与 `examples/ingest_custom_jsonl.py`;保真契约见 `CONTRACT.md`。

## 许可证
本项目采用 **PolyForm Strict License 1.0.0**(全文见 [`LICENSE.md`](LICENSE.md))——**源码可见(source-available),不是标准开源**:

- ✅ 个人 / 非商业**免费使用**;
- 🚫 **禁止商业使用**;
- 🚫 **禁止二次开发分发**(不得分发本软件,也不得分发基于它的修改 / 衍生作品)。

---

**十页 AI 出品** · 官网:<https://shiyeai.cn>
