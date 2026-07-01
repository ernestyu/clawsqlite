# clawsqlite（knowledge）

**语言：**[English](README.md) | 中文

`clawsqlite` 是一个围绕 SQLite 打造的 CLI 工具箱。它有两层：

- `clawsqlite admin ...`：当前 knowledge component 的管理员维护面，读取同一份 `clawsqlite.toml`，面向诊断、维护和恢复。
- `clawsqlite knowledge ...`：面向文章、笔记、想法入库的知识库应用，读取 `clawsqlite.toml`。

这一层次很重要：`sqlite3` 才是系统级任意 SQLite 工具；`clawsqlite admin`
不是第二个 sqlite3，而是当前知识库 component 的维护接口。它和
Knowledge 共享同一个 component root、同一个 `clawsqlite.toml`、同一个
root / db / articles_dir / 运行配置。`--db`、`--root` 等路径参数只作为
明确的调试或恢复覆盖，不是常规入口。

维护边界也要清楚：`admin index rebuild` 是 DB-only / index-only 的索引维护，
默认只重建 FTS schema 与 base table 交集里的 DB-backed 字段，不读取或恢复
Markdown 正文文件；正文文件缺失、孤儿文件、DB 与文件系统不一致这类问题走
`admin fs list-orphans / repair / gc`。

---

## 1. 它解决什么问题

你可以把日常看到的好文章、自己的想法、和 Agent 讨论后的总结放进本地知识库：

- 正文保存为 Markdown 文件；
- 元数据保存进 SQLite；
- 通过 FTS5 做全文检索；
- 可选通过 embedding 做向量检索；
- 入库时用小模型生成摘要、标签、关键观点、实体等结构化字段；
- 日后让 Agent 用 `clawsqlite knowledge record search ...` 找回相关内容。

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

配置查找只有一条规则：Knowledge 命令只读取当前 component root 下的
`./clawsqlite.toml`。直接使用时当前目录应是 clawsqlite repo 根目录；通过
OpenClaw/ClawHub 使用时当前目录应是 skill 目录。不会向父目录搜索，也没有 CLI 配置覆盖入口。

创建模板：

```bash
clawsqlite knowledge maintenance init-config --out clawsqlite.toml
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
summary_target_chars = 3600
tags_mode = "llm"
tag_count = 8
allowed_categories = [
  "web_article",
  "note",
  "thought",
  "discussion_summary",
  "document",
  "reference",
  "repo",
  "paper",
  "social_post",
]
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

[fts]
jieba = "auto"

[search.query]
tag_min = 8
tag_max = 12

[search.weights.mode1] # LLM + Embedding
vec = 0.45
fts = 0.25
tag = 0.15
priority = 0.03
recency = 0.02

[search.weights.mode2] # LLM + no Embedding
fts = 0.60
tag = 0.25
priority = 0.08
recency = 0.07

[search.weights.mode3] # no LLM + Embedding
vec = 0.45
fts = 0.25
tag = 0.15
priority = 0.03
recency = 0.02

[search.weights.mode4] # no LLM + no Embedding
fts = 0.60
tag = 0.25
priority = 0.08
recency = 0.07

[search.tag]
vec_fraction = 0.70
fts_log_alpha = 5.0

[interest]
cluster_algo = "kmeans++"
tag_weight = 0.75
use_pca = true
pca_explained_variance_threshold = 0.95
min_size = 8
max_clusters = 50
kmeans_random_state = 42
kmeans_n_init = 10
kmeans_max_iter = 300
enable_post_merge = true
merge_distance_threshold = 0.06
hierarchical_linkage = "average"
hierarchical_distance_threshold = 0.20
merge_alpha = 0.40

[report]
lang = "en"
```

说明：

- `root` 相对路径按配置文件所在目录解析。
- `db` 和 `articles_dir` 相对路径按 `root` 解析。
- Knowledge 的 root / db / articles 路径不再通过 CLI 覆盖；要修改就改私有 `clawsqlite.toml`。
- `clawsqlite.toml` 是唯一项目配置文件；本项目不再提供额外环境模板，
  CLI 也不会自动读取 dot-env 文件作为第二配置源。

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
clawsqlite knowledge record ingest ... --allow-heuristic
clawsqlite knowledge record ingest ... --allow-missing-embedding
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
- `category`
- `content_type`

`summary` 是后续 embedding 的默认文本来源。摘要目标长度由配置控制：

```toml
[ingest]
summary_target_chars = 3600
tag_count = 8
```

这些值不是写死在代码里的，后续可以按模型和知识库风格调整。
strict 入库时，最终 tags 必须由 LLM 生成，数量必须等于 `tag_count`。
如果直接输入的短文本，或内容类型为 `note`、`thought`、
`discussion_summary` 的短内容，正文短于 `summary_target_chars`，summary 会
直接使用清理后的正文，避免短想法被不必要地改写丢细节。URL / web_article
默认仍由 LLM 摘要，避免把抓取噪声直接写进 summary。

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
   clawsqlite knowledge maintenance init-config --out clawsqlite.toml
   ```

3. 在私有配置里直接填写 `[knowledge]`、`[llm]`、`[embedding]`，包括真实 API key。

4. 自检：

   ```bash
   clawsqlite knowledge maintenance doctor --json
   ```

5. 严格入库：

   ```bash
   clawsqlite knowledge record ingest \
     --text "这里是一段值得长期保存的想法。" \
     --title "第一次想法记录" \
     --category thought \
     --json
   ```

如果只是本地无网络测试，可以明确降级：

```bash
clawsqlite knowledge record ingest \
  --text "你好，clawsqlite" \
  --title "测试笔记" \
  --category note \
  --gen-provider off \
  --allow-heuristic \
  --allow-missing-embedding \
  --json
```

---

## 6. 常用命令

### 入库

```bash
clawsqlite knowledge record ingest --url "https://example.com/article" --category web_article --json
clawsqlite knowledge record ingest --text "一段想法" --category thought --json
```

常用参数：

- `--url` / `--text`：二选一
- `--title` / `--summary` / `--category` / `--priority`
- `--gen-provider {openclaw,llm,off}`：默认来自配置
- `--max-summary-chars`：覆盖配置里的 `summary_target_chars`
- `--scrape-cmd`：覆盖配置里的抓取命令
- `--update-existing`：URL 已存在时刷新同一条记录
- `--allow-heuristic` / `--allow-missing-embedding`：显式降级

strict 模式下，人工 title/category 只是给 LLM 的提示，不会覆盖最终
metadata。最终 title、tags、category、content_type 必须由 LLM 生成；tags
数量必须等于 `[ingest].tag_count`，category 和 content_type 必须一致，并且
必须属于 `[ingest].allowed_categories`。成功的 JSON 输出会包含 `config_path`、
`root`、`db`、`articles_dir`、`generation_quality`、
`embedding_runtime_enabled`、`embedding_required`，方便 Agent 核对实际写入位置、
生成质量，以及 embedding 是策略要求还是运行时实际可用。

### 自检

```bash
clawsqlite knowledge maintenance doctor --json
```

默认 doctor 只做轻量配置和 schema 检查：它会判断 `[llm]` / `[embedding]`
字段是否完整，但不会主动请求外部模型服务。只有显式传入 `--check-llm` 或
`--check-embedding` 时，才会执行更重的 HTTP roundtrip 检查。

### 搜索

```bash
clawsqlite knowledge record search "我之前关于 SQLite Agent 的想法" --mode hybrid --json
```

模式：

- `hybrid`：FTS + 向量混合，embedding 不可用时提示并退到 FTS；
- `fts`：只用全文检索；
- `vec`：只用向量，embedding 不可用时直接失败。

### 查看与导出

```bash
clawsqlite knowledge record show --id 12 --full --json
clawsqlite knowledge record export --id 12 --format md --out /tmp/article.md
```

### 更新

```bash
clawsqlite knowledge record update --id 12 --tags "sqlite,agent,knowledge" --json
clawsqlite knowledge record update --id 12 --regen all --json
```

严格配置下，`--regen` 默认也会走配置里的 LLM 生成路径。需要降级时必须显式加 `--allow-heuristic`。

### 派生数据修复

```bash
clawsqlite knowledge record update --id 12 --regen summary --json
clawsqlite knowledge record update --id 12 --regen tags --json
clawsqlite knowledge record update --id 12 --regen embedding --json
clawsqlite knowledge record update --id 12 --regen all --json
clawsqlite knowledge maintenance reindex --fix-missing --json
```

单条记录用 `update --regen`；批量缺失派生字段或索引用 `reindex --fix-missing`。
`generation_quality` 是来源/生成方式元数据，不再作为独立顶层命令入口。

### 维护

```bash
clawsqlite knowledge maintenance reindex --check --json
clawsqlite knowledge maintenance cleanup --days 3 --dry-run
```

`reindex --fix-missing` 会按配置里的生成器补缺失字段；严格配置下同样要求 LLM，除非显式加 `--allow-heuristic`。

---

## 7. OpenClaw Skill 说明层

本仓库也提供一个很薄的 ClawHub/OpenClaw Skill 说明层：
[skills/clawsqlite-knowledge](skills/clawsqlite-knowledge/SKILL.md)。

不要混淆两个名字相近的目录：

- `clawsqlite_knowledge/` 是 Knowledge 应用的 Python 核心实现；
- `skills/clawsqlite-knowledge/` 只是面向 Agent 的薄说明层。

Agent 应先 `cd` 到该 skill 目录，把它作为 component root，然后直接运行
`clawsqlite knowledge ...`；Skill 不提供额外运行时脚本，不直接读数据库，
不生成标签，不猜路径，也不默认降级。

---

## 8. 兴趣簇

`build-interest-clusters` 仍然保留，用于基于 `articles_vec` 与 `articles_tag_vec` 构建兴趣簇：

```bash
clawsqlite knowledge analysis build-interest-clusters --algo kmeans++ --json
clawsqlite knowledge analysis inspect-interest-clusters --vec-dim 1024
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
