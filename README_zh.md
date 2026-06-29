# clawsqlite（knowledge）

**语言：**[English](README.md) | 中文

`clawsqlite` 是一个围绕 SQLite 打造的 CLI 工具箱。它有两层：

- `clawsqlite_plumbing`：通用 SQLite / FTS / 向量 / 文件系统基础命令，不读取知识库配置。
- `clawsqlite_knowledge`：面向文章、笔记、想法入库的知识库应用，读取 `clawsqlite.toml`。

这一层次很重要：底层 plumbing 应该能服务任何 SQLite 数据库；Knowledge 只负责“网页/想法入库、检索、维护”这一类知识库工作流。

---

## 1. 它解决什么问题

你可以把日常看到的好文章、自己的想法、和 Agent 讨论后的总结放进本地知识库：

- 正文保存为 Markdown 文件；
- 元数据保存进 SQLite；
- 通过 FTS5 做全文检索；
- 可选通过 embedding 做向量检索；
- 入库时用小模型生成摘要、标签、关键观点、实体等结构化字段；
- 日后让 Agent 用 `clawsqlite knowledge search ...` 找回相关内容。

核心入口：

```bash
clawsqlite knowledge ...
```

---

## 2. 配置文件优先

Knowledge 命令默认会读取 `clawsqlite.toml`。不要让 Agent 猜数据库路径。
真实的 `clawsqlite.toml` 是本地私有配置源，已经被 `.gitignore` 忽略，
可以直接放真实 API key。`clawsqlite.toml.example` 只是公开示例模板，
里面只应该保留占位值和说明。

配置查找顺序：

1. `--config /path/to/clawsqlite.toml`
2. 环境变量 `CLAWSQLITE_CONFIG`
3. 从当前目录向上查找最近的 `clawsqlite.toml`

创建模板：

```bash
clawsqlite knowledge init-config --out clawsqlite.toml
# 或者
cp clawsqlite.toml.example clawsqlite.toml
```

示例：

```toml
[knowledge]
root = "./knowledge_data"
db = "knowledge.sqlite3"
articles_dir = "articles"

[ingest]
require_llm = true
require_embedding = true
summary_mode = "llm"
summary_target_chars = 800
tags_mode = "llm"
fallback = "fail"

[llm]
base_url = "https://llm.example.com/v1"
model = "your-small-llm"
api_key = ""  # 在私有 clawsqlite.toml 中填入真实 key
timeout_seconds = 90
context_window_chars = 24000
prompt_reserved_chars = 4000
chunk_overlap_chars = 500

[embedding]
base_url = "https://embed.example.com/v1"
model = "your-embedding-model"
api_key = ""  # 在私有 clawsqlite.toml 中填入真实 key
dim = 1024
timeout_seconds = 300
content = "summary"

[scraper]
# cmd = "node /path/to/scrape.js"
```

说明：

- `root` 相对路径按配置文件所在目录解析。
- `db` 和 `articles_dir` 相对路径按 `root` 解析。
- `--root` / `--db` / `--articles-dir` 仍保留为调试覆盖参数，但正常 Agent 使用应优先依赖 `clawsqlite.toml`。
- `.env` 仍会自动读取，但只建议用于可选底层调参；LLM、embedding、scraper、路径和入库策略都应写在私有 `clawsqlite.toml` 里。

---

## 3. 严格入库是默认行为

默认模板是严格模式：

```toml
[ingest]
require_llm = true
require_embedding = true
fallback = "fail"
```

严格模式下：

- LLM 生成字段失败，命令失败；
- embedding 配置缺失或 vec 同步失败，命令失败；
- Agent 不应该自己编标签，也不应该无声退回 TextRank / jieba 标签。

如果你明确想要降级入库，必须在命令上写出来：

```bash
clawsqlite knowledge ingest ... --allow-heuristic
clawsqlite knowledge ingest ... --allow-missing-embedding
```

这两个参数的含义是“我主动接受质量降低”，适合无网络测试或用户明确要求的场景。

---

## 4. LLM 摘要与分块

LLM 入库会从全文生成结构化字段：

- `title`
- `summary`
- `tags`
- `key_claims`
- `entities`
- `content_type`

`summary` 是后续 embedding 的默认文本来源。摘要目标长度由配置控制：

```toml
[ingest]
summary_target_chars = 800
```

这个值不是写死在代码里的，后续可以按模型和知识库风格调整。

长文处理使用配置里的上下文预算：

```toml
[llm]
context_window_chars = 24000
prompt_reserved_chars = 4000
chunk_overlap_chars = 500
```

逻辑是：

1. 如果全文长度小于 `context_window_chars - prompt_reserved_chars`，直接一次发给 LLM；
2. 如果超过，就按预算切块；
3. 先总结每个 chunk；
4. 再从 chunk summaries 合成最终摘要、标签、关键观点等字段。

这比固定截断文章前 1200 字更适合“整篇文章入库后供未来语义检索”。

---

## 5. 快速开始

1. 克隆并进入仓库：

   ```bash
   git clone git@github.com:ernestyu/clawsqlite.git
   cd clawsqlite
   ```

2. 创建配置：

   ```bash
   clawsqlite knowledge init-config --out clawsqlite.toml
   ```

3. 在私有配置里直接填写 `[knowledge]`、`[llm]`、`[embedding]`，包括真实 API key。

4. 自检：

   ```bash
   clawsqlite knowledge doctor --json
   ```

5. 严格入库：

   ```bash
   clawsqlite knowledge ingest \
     --text "这里是一段值得长期保存的想法。" \
     --title "第一次想法记录" \
     --category thought \
     --json
   ```

如果只是本地无网络测试，可以明确降级：

```bash
clawsqlite knowledge ingest \
  --text "你好，clawsqlite" \
  --title "测试笔记" \
  --category test \
  --tags demo \
  --gen-provider off \
  --allow-heuristic \
  --allow-missing-embedding \
  --json
```

---

## 6. 常用命令

### 入库

```bash
clawsqlite knowledge ingest --url "https://example.com/article" --category web --json
clawsqlite knowledge ingest --text "一段想法" --category thought --json
```

常用参数：

- `--config`：显式指定 `clawsqlite.toml`
- `--url` / `--text`：二选一
- `--title` / `--summary` / `--tags` / `--category` / `--priority`
- `--gen-provider {openclaw,llm,off}`：默认来自配置
- `--max-summary-chars`：覆盖配置里的 `summary_target_chars`
- `--scrape-cmd`：覆盖配置里的抓取命令
- `--update-existing`：URL 已存在时刷新同一条记录
- `--allow-heuristic` / `--allow-missing-embedding`：显式降级

### 搜索

```bash
clawsqlite knowledge search "我之前关于 SQLite Agent 的想法" --mode hybrid --json
```

模式：

- `hybrid`：FTS + 向量混合，embedding 不可用时提示并退到 FTS；
- `fts`：只用全文检索；
- `vec`：只用向量，embedding 不可用时直接失败。

### 查看与导出

```bash
clawsqlite knowledge show --id 12 --full --json
clawsqlite knowledge export --id 12 --format md --out /tmp/article.md
```

### 更新

```bash
clawsqlite knowledge update --id 12 --tags "sqlite,agent,knowledge" --json
clawsqlite knowledge update --id 12 --regen all --json
```

严格配置下，`--regen` 默认也会走配置里的 LLM 生成路径。需要降级时必须显式加 `--allow-heuristic`。

### 重建低质量旧记录

```bash
clawsqlite knowledge rebuild-quality --dry-run --json
clawsqlite knowledge rebuild-quality --json
```

这个命令用于把旧的启发式记录升级为 LLM 质量记录：重生成 summary/tags/key_claims/entities，重写 Markdown metadata，并刷新 FTS / embedding。

### 维护

```bash
clawsqlite knowledge reindex --check --json
clawsqlite knowledge maintenance prune --days 3 --dry-run
```

`reindex --fix-missing` 会按配置里的生成器补缺失字段；严格配置下同样要求 LLM，除非显式加 `--allow-heuristic`。

---

## 7. Agent 使用契约

面向 Agent 的更短说明见 [AGENT_USAGE.md](AGENT_USAGE.md)。核心规则：

- 先运行 Knowledge 命令，让它读取 `clawsqlite.toml`；
- 不要自己猜 DB 文件名；
- 遇到 `ERROR_KIND: config_required` / `llm_required` / `embedding_required` 时，把错误报告给用户；
- 不要无声降级生成标签；
- 只有用户明确允许时才加 `--allow-heuristic` 或 `--allow-missing-embedding`。

本仓库也提供一个很薄的 ClawHub/OpenClaw Skill 适配层：
[skills/clawsqlite-knowledge](skills/clawsqlite-knowledge/SKILL.md)。
它只开放 `ingest_url`、`ingest_text`、`search`、`show`、`doctor`，并把 JSON 请求转换为
`clawsqlite knowledge` 调用；不直接读数据库，不生成标签，不猜路径，也不默认降级。

---

## 8. 兴趣簇

`build-interest-clusters` 仍然保留，用于基于 `articles_vec` 与 `articles_tag_vec` 构建兴趣簇：

```bash
clawsqlite knowledge build-interest-clusters --algo kmeans++ --json
clawsqlite knowledge inspect-interest-clusters --vec-dim 1024
```

当前兴趣簇更适合作为分析辅助，不应被理解为稳定的长期兴趣分类体系。建议先把入库质量、摘要质量、标签质量做好，再逐步优化兴趣系统。

---

## 9. 文件命名

Markdown 文件命名：

```text
<id>__<slug>.md
```

标题和正文仍以 DB 和 Markdown 内容为准，文件名只是方便浏览和维护。

---

## License

MIT © Ernest Yu
