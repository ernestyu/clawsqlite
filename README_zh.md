# clawsqlite（knowledge）

`clawsqlite` 是一个围绕 SQLite 打造的 CLI 工具箱，主要面向
[OpenClaw](https://github.com/openclaw/openclaw) 场景。

当前仓库内置的第一个应用是 **本地 Markdown + SQLite 知识库**。

- 面向用户 / Skill 的入口统一为：`clawsqlite knowledge ...`

下面的文档仍然以“知识库应用”的视角展开。

这个知识库主要帮你解决两件事：

- 把网页和零散笔记统一存成 Markdown 文件 + SQLite 记录，放在一个可控的根目录里；
- 提供快速的全文检索和可选的向量检索，让你和 Agent 都能稳定地查到这些内容。

核心能力包括：

- 将 URL 或原始文本入库为 Markdown 文件 + 数据库记录
- 基于 FTS5 的全文检索
- 可选的向量检索（依赖外部 Embedding 服务）
- 基于启发式或小模型的小结（title / summary，tags 可选由小模型生成）生成与刷新
- 日常维护命令（检查 / 修复 / 重建索引，以及清理孤儿文件）

> 状态：已经在真实的 OpenClaw 环境中使用，接口尽量保持简单、稳定。

---

## 1. 特性概览

- **纯 SQLite 后端**
  - `articles` 表：保存主记录（标题、摘要、标签、分类、路径、时间戳等）
  - `articles_fts`：FTS5 全文索引，用于按标题/标签/摘要检索
  - `articles_vec`：vec0 向量表（可选），用于向量检索

- **Markdown 正文存储**
  - 每篇文章保存为一个 Markdown 文件：`<articles_dir>/<id>__<slug>.md`
  - 文件包含一个简短的 `--- METADATA ---` 区块和 `--- MARKDOWN ---` 正文区

- **统一根目录**
  - 所有数据放在同一个根目录下：根目录可通过 `--root` / `CLAWSQLITE_ROOT` 配置
  - 数据库默认：`<root>/knowledge.sqlite3`
  - Markdown 目录默认：`<root>/articles`

- **可选 Embedding 和小模型**
  - Embedding：通过兼容 OpenAI 的 `/v1/embeddings` HTTP 接口
  - 小模型（small LLM）：通过兼容 `/v1/chat/completions` 的接口生成标签

- **命令行优先**
  - 核心子命令：`ingest`，`search`，`show`，`export`，`update`，`delete`，`reindex`，`maintenance`

---

## 2. 运行环境

知识库应用设计时默认运行在 OpenClaw 容器内，但也可以在普通
Linux 环境中独立使用。

必要条件：

- Python 3.10+
- SQLite 内置 FTS5 支持

Python 依赖：

- `jieba`（可选但强烈推荐，用于中文标签和关键词抽取）
- `pypinyin`（可选，用于中文标题生成拼音 slug）

推荐但可选的 sqlite 扩展：

- simple tokenizer 扩展：`libsimple.so`（适合中英混合文本的分词）
- sqlite-vec 的 vec0 扩展：`vec0.so`

默认假设的路径（可以通过参数/环境变量覆盖）：

- Tokenizer 扩展：`/usr/local/lib/libsimple.so`
- vec0 扩展：自动在 `/app/node_modules/**/vec0.so` 或系统库路径中探测

在一个全新的环境中，一般需要：

- 安装 jieba：

  ```bash
  pip install jieba
  ```

- 准备 `libsimple.so` 和 `vec0.so`：
  - 在 OpenClaw 官方容器中，这两个扩展已经预装好；
  - 在自建环境中，可以：
    - 优先查一下你所在发行版是否提供 sqlite-vec / simple tokenizer 的二进制包；
    - 或者根据上游文档自行编译：
      - sqlite-vec：<https://github.com/asg017/sqlite-vec>
      - simple tokenizer：参考 OpenClaw 文档中关于 `libsimple.so` 的构建说明。

如果缺少这些扩展，知识库应用会自动降级：

- FTS：使用 SQLite 内建 tokenizer；
- 若 `jieba` 可用，可在 Python 侧对 CJK 文本做分词以提升召回（由 `CLAWSQLITE_FTS_JIEBA=auto|on|off` 控制）；
- 向量检索：在 vec0 不可用时自动关闭，仅使用 FTS。

---

## 3. 安装与启动

### 3.1 通过 PyPI 安装（推荐给一般用户）

发布到 PyPI 后，你可以直接在环境里安装并使用：

```bash
pip install clawsqlite

# 然后
clawsqlite knowledge --help
```

这会在当前环境中安装一个 `clawsqlite` 命令行入口，方便在任意目录调用。

### 3.2 从源码运行（开发 / OpenClaw 工作目录）

克隆本仓库：

```bash
git clone git@github.com:ernestyu/clawsqlite.git
cd clawsqlite
```

在 OpenClaw 工作目录中，这个仓库通常位于：

```text
/home/node/.openclaw/workspace/clawsqlite
```

推荐使用仓库自带的入口脚本运行知识库应用：

```bash
# 在仓库根目录
./bin/clawsqlite knowledge --help

# 或者显式指定 Python（例如虚拟环境）
CLAWSQLITE_PYTHON=/opt/venv/bin/python ./bin/clawsqlite knowledge --help
```

Embedding / 抓取等配置建议通过外部环境变量
（例如 OpenClaw 的 Agent 配置）来管理。

---

## 4. 配置

### 4.1 根目录与路径

知识库应用的根目录、数据库路径、Markdown 目录均可通过 CLI 参数或
环境变量配置，优先级如下：

**根目录：**

1. CLI 参数：`--root`
2. 环境变量：`CLAWSQLITE_ROOT`（推荐）
3. 默认：`<当前工作目录>/knowledge_data`

**数据库路径：**

- `--db` > `CLAWSQLITE_DB` > `<root>/knowledge.sqlite3`

**Markdown 目录：**

- `--articles-dir` > `CLAWSQLITE_ARTICLES_DIR` > `<root>/articles`

### 4.2 项目级 `.env`

clawsqlite knowledge 推荐使用项目根目录下的 `.env` 文件集中配置。可以先从例子复制：

```bash
cp ENV.example .env
# 然后编辑 .env
```

`ENV.example` 中包含：

```env
# Embedding 服务（用于向量检索）
EMBEDDING_BASE_URL=https://embed.example.com/v1
EMBEDDING_MODEL=your-embedding-model
EMBEDDING_API_KEY=sk-your-embedding-key
CLAWSQLITE_VEC_DIM=1024

# 小模型（可选，用于生成标签）
SMALL_LLM_BASE_URL=https://llm.example.com/v1
SMALL_LLM_MODEL=your-small-llm
SMALL_LLM_API_KEY=sk-your-small-llm-key

# 根目录及路径覆盖（可选）
# CLAWSQLITE_ROOT=/path/to/knowledge_root
# CLAWSQLITE_DB=/path/to/knowledge.sqlite3
# CLAWSQLITE_ARTICLES_DIR=/path/to/articles
```

运行时，`clawsqlite knowledge ...`（以及 `python -m clawsqlite_cli`）会自动加载
当前工作目录下的 `.env`，并覆盖同名环境变量；整体优先级为：CLI 参数 > 项目 `.env` > 进程环境变量。
OpenClaw 场景下通常通过 agent 配置注入环境变量。

### 4.3 标签生成（长摘要 + LLM / jieba 降级）

入库抓取到正文后，会先从正文构造“长摘要”（写入 summary 字段）：
- 取正文开头约 1200 字（到自然段结尾；可能略多或略少）
- 再加上文章最后一段（通常是总结）

标签 tags 就从这个长摘要生成（不会直接用全文）：
- 满配：`--gen-provider llm` 且配置 `SMALL_LLM_BASE_URL/SMALL_LLM_MODEL/SMALL_LLM_API_KEY` 时，用小模型从长摘要生成 tags（要求按重要性降序输出）。
- 降级：未配置 LLM 或调用失败时，会输出 WARNING/NEXT 提示，并使用 jieba v6 启发式算法从长摘要提取 tags；若未安装 jieba，则退回轻量关键词提取。

查询侧（search）会先从自然语言 query 抽取关键词（v4 纯算法；CJK 优先用 jieba），例如 `["网络", "爬虫", "文章"]`：
- 这些关键词用于 FTS 关键词检索、tag 匹配与加权打分
- 向量检索仍会做 embedding，但输入是“原始 query + 关键词拼接”，以降低虚词干扰同时保留上下文

### 4.3 向量检索与打分（Embedding + 评分逻辑）

当你需要向量检索（`articles_vec`）时，需要配置 Embedding 服务：

当你需要向量检索（`articles_vec`）时，需要配置 Embedding 服务：

必需环境变量：

- `EMBEDDING_MODEL`：服务端模型名
- `EMBEDDING_BASE_URL`：基础 URL，例如 `https://embed.example.com/v1`
- `EMBEDDING_API_KEY`：访问令牌
- `CLAWSQLITE_VEC_DIM`：Embedding 维度（如 BAAI/bge-m3 为 1024）

知识库应用会：

- 在 `clawsqlite_knowledge.embed.get_embedding()` 中调用上述 HTTP 接口获取向量；
- 使用 `CLAWSQLITE_VEC_DIM` 创建
  `articles_vec` 表的 `embedding float[DIM]` 列；
- 对摘要和标签分别计算向量：
  - 摘要向量写入 `articles_vec`；
  - 标签向量写入 `articles_tag_vec`（同一维度）；
- 在归一化距离时，先做 `1/(1+d)`（d 为 L2 距离），再经过一个以 0.5 为中心的
  **Logistic Sigmoid**，让“真正相似”的条目得分明显高于一般相似；
- 在打分阶段，标签通道会拆成：
  - 标签语义得分（tag vector）；
  - 标签字面匹配得分（tag lexical），并可通过
    `CLAWSQLITE_TAG_FTS_LOG_ALPHA` 对标签字面分做 `ln(1+αx)/ln(1+α)` 的 log 压缩
    （默认 α=5.0，α<=0 时关闭压缩）；
- 在 `embedding_enabled()` 为假或 vec 表不存在时自动降级为纯 FTS 模式。

如果上述任一变量缺失：

- `embedding_enabled()` 返回 `False`；
- `ingest` 不会请求 Embedding，也不会写 `articles_vec` / `articles_tag_vec`；
- `search` 的 `mode=hybrid` 会自动退化为 FTS，并输出 NEXT 提示。

### 4.4 小模型（Small LLM）配置

如果希望用小模型生成/优化标签，可以配置：

```env
SMALL_LLM_BASE_URL=https://llm.example.com/v1
SMALL_LLM_MODEL=your-small-llm
SMALL_LLM_API_KEY=sk-your-small-llm-key
```

---

### 4.5 兴趣簇（interest clusters）配置与调试

在知识库已经积累了一定文章（例如几十到几百篇）后，可以基于摘要和
标签 embedding 构建“兴趣簇”，把知识 base 看成若干兴趣主题：

- 簇定义存储在：
  - `interest_clusters(id,label,size,summary_centroid,created_at,updated_at)`
  - `interest_cluster_members(cluster_id,article_id,membership)`
- 上层应用（例如个人读报雷达）可以把这些簇当成 topic/兴趣点来使用。

构建命令：

```bash
clawsqlite knowledge build-interest-clusters \
  --db /path/to/clawkb.sqlite3 \
  --min-size 5 \
  --max-clusters 16
```

并可通过以下 env 或 `.env` 控制行为（CLI 参数优先）：

- `CLAWSQLITE_INTEREST_MIN_SIZE`（默认 5）
  - 每个簇的最小文章数，小于该值的小簇会被重分配到最近大簇；
- `CLAWSQLITE_INTEREST_MAX_CLUSTERS`（默认 16）
  - 第一阶段 k-means 的簇上限，实际 k0= `min(max_clusters, n//min_size)`；
- `CLAWSQLITE_INTEREST_MERGE_DISTANCE`（默认 0.06）
  - 基于簇心余弦距离的“合并阈值”，使用 `1 - cos_sim`；
  - 值越小，簇越细；值越大，簇越粗，最终簇数越少；
- `CLAWSQLITE_INTEREST_MERGE_ALPHA`（默认 0.4）
  - 用于自动建议 merge 阈值的比例系数：
    - 根据当前兴趣簇计算每簇的 `mean_radius`（成员到簇心的平均 1 - cos 距离）；
    - 取其中位数作为典型半径 `median(mean_radius)`；
    - 推荐阈值约为 `merge_distance ≈ alpha * median(mean_radius)`。

调试与可视化：

```bash
clawsqlite knowledge inspect-interest-clusters \
  --db /path/to/clawkb.sqlite3 \
  --vec-dim 1024
```

提示：该命令依赖 `numpy`；如需生成 PNG 图，还需要 `matplotlib`。

- 仅统计：`pip install 'clawsqlite[analysis]'`
- 统计 + 绘图：`pip install 'clawsqlite[analysis,plot]'`

- 打印每簇：`size / n_members / mean_radius / max_radius`；
- 打印簇心两两 `1 - cos` 距离的 `min / max / median`；
- 若安装了 matplotlib，则生成一张 PCA 2D 散点图：
  - 点位置：簇心的 (PC1,PC2) 坐标；
  - 点大小：与簇大小成比例（开根号缩放）；
  - 点颜色：映射 `mean_radius`，色条标签为 `mean_radius (1 - cos)`；
  - PNG 默认输出为：`./interest_clusters_pca.png`。

可以加 `--no-plot` 只看数值不画图。

实际建议的调参流程：

1. 先用当前 env/参数跑一轮 `build-interest-clusters`；
2. 使用 `inspect-interest-clusters` 观察簇大小分布、簇内半径和簇间距离；
3. 使用 `tests/test_interest_merge_suggest.py` 或类似逻辑（按
   `alpha * median(mean_radius)`）估算一个合适的 merge 阈值；
4. 更新 `.env` 中的 `CLAWSQLITE_INTEREST_*` 参数，重新构建并观察结构变化。

---

然后在入库或更新时使用：

```bash
--gen-provider llm
```

clawsqlite knowledge 的 `gen-provider=llm` 会使用启发式生成 title/summary，并通过 OpenAI-compatible chat completions API（SMALL_LLM_*）调用小模型生成 `tags`（要求只输出严格 JSON：`{"tags": [...]}`）。

如果未配置 SMALL_LLM，则：

- `gen-provider=openclaw` 会使用纯启发式；
- `gen-provider=llm` 未配置 SMALL_LLM 时会自动降级为 jieba 标签提取，并输出 NEXT 提示。

### 4.5 抓取脚本（Scraper）配置

知识库应用本身不负责网页抓取；对于 `--url` 模式，会调用你提供的脚本。

配置方式：

- CLI：`--scrape-cmd`
- 环境变量：`CLAWSQLITE_SCRAPE_CMD`

示例：

```env
CLAWSQLITE_SCRAPE_CMD="node /path/to/scrape.js --some-flag"
```

行为说明：

- `.env` 中的值支持加引号，clawsqlite knowledge 会在载入时去掉外层引号；
- 使用 `shlex.split()` 构造 argv，默认不使用 `shell=True`；
- 如果命令中包含 `{url}`，会用实际 URL 替换该占位符；
- 否则会把 URL 追加为 argv 的最后一个参数。

抓取脚本输出支持两种格式：

1. **推荐：结构化文本**

   ```text
   --- METADATA ---
   Title: 文章标题
   Author: 作者
   ...

   --- MARKDOWN ---
   # 一级标题
   正文……
   ```

2. **简化格式**

   ```text
   Title: 文章标题
   # 一级标题
   正文……
   ```

知识库应用会从这两种格式中解析出标题和 Markdown 正文。

---

## 5. 快速上手

### 5.1 最小配置

1. 克隆仓库并进入目录：

   ```bash
   git clone git@github.com:ernestyu/clawsqlite.git
   cd clawsqlite
   ```

2. （可选）在运行环境中配置好根目录和 Embedding/LLM 相关 env，或者
   直接在运行命令时通过 `--root/--db/--articles-dir` 传入。这里不再
   强制依赖项目内置的 `.env` 加载逻辑。

3. 第一次入库（纯文本）：

   ```bash
   clawsqlite knowledge ingest \
     --text "你好，clawsqlite" \
     --title "第一次笔记" \
     --category test \
     --tags demo \
     --gen-provider off \
     --json
   ```

   这一步会：

   - 创建 `<root>/knowledge.sqlite3`；
   - 在 `<root>/articles` 下生成 `000001__第一次笔记.md`（实际文件名取决于 slug）；
   - 写入 FTS 索引（如果 Embedding 已配置且 vec 表可用，会一起写入向量索引）。

4. 搜索刚才那条记录：

   ```bash
   clawsqlite knowledge search "clawsqlite" --mode fts --json
   ```

### 5.2 从 URL 入库

假设 `.env` 中已经配置了抓取脚本：

```bash
clawsqlite knowledge ingest \
  --url "https://example.com/article" \
  --category web \
  --tags example \
  --gen-provider openclaw \
  --json
```

入库流程：

- 调用抓取脚本获取网页内容；
- 解析标题与 Markdown 正文；
- （必要时）基于正文生成摘要和标签；
- 写入数据库 + Markdown 文件；
- 同步 FTS 与 vec 索引（如果向量功能已启用）。

### 5.3 URL 重抓与刷新（`--update-existing`）

当你知道某篇网页已经更新，希望在保持 URL/ID 不变的前提下刷新内容，可以：

```bash
clawsqlite knowledge ingest \
  --url "https://example.com/article" \
  --gen-provider openclaw \
  --update-existing \
  --json
```

语义：

- 若数据库中存在同一 `source_url` 的记录（包括软删记录）：
  - 复用原来的 `id`；
  - 更新 `tags` / `category` / `priority`；
  - 重新生成 Markdown 文件；
  - 同步 FTS/vec 索引；
  - 旧 Markdown 文件会重命名为 `.bak_<timestamp>` 作为备份。
- 若不存在该 URL，则行为等同普通入库。

`source_url` 在非空且非 `Local` 时有唯一约束，保证一个 URL 最多只对应一条记录。

---

## 6. CLI 子命令总览

所有子命令共用一组全局参数：

- `--root`：根目录
- `--db`：数据库路径
- `--articles-dir`：Markdown 目录
- `--tokenizer-ext`：tokenizer 扩展路径
- `--vec-ext`：vec0 扩展路径
- `--json`：输出 JSON
- `--verbose`：输出更详细日志

可以使用：

```bash
clawsqlite knowledge <command> --help
```

查看详细说明。

### 6.1 ingest

```bash
clawsqlite knowledge ingest --url URL [选项]
clawsqlite knowledge ingest --text TEXT [选项]
```

常用参数：

- `--url` / `--text`：二选一
- `--title` / `--summary` / `--tags` / `--category` / `--priority`
- `--gen-provider {openclaw,llm,off}`：字段生成方式
- `--max-summary-chars`：摘要最大长度
- `--scrape-cmd`：抓取命令（或用 `CLAWSQLITE_SCRAPE_CMD`）
- `--update-existing`：URL 已存在时更新同一条记录

### 6.2 search

```bash
clawsqlite knowledge search "查询词" --mode hybrid --topk 20 --json
```

模式：

- `hybrid`：默认，FTS + vec 混合
- `fts`：只用 FTS
- `vec`：只用向量（需要 Embedding 和 vec 表可用）

常用参数：

- `--topk`：返回结果条数
- `--candidates`：初始候选集大小
- `--llm-keywords {auto,on,off}`：是否用小模型扩展 FTS 查询关键词
- `--gen-provider`：在 `llm-keywords=auto/on` 时使用的小模型提供者
- 过滤：`--category` / `--tag` / `--since` / `--priority` / `--include-deleted`

### 6.3 show / export

- `show`：查看单条记录，`--full` 时附带 Markdown 正文
- `export`：导出为 `.md` 或 `.json` 文件

示例：

```bash
clawsqlite knowledge show --id 12 --full --json
clawsqlite knowledge export --id 12 --format md --out /tmp/article.md
```

### 6.4 update

```bash
clawsqlite knowledge update --id ID [补丁参数] [--regen ...]
```

行为：

- `id` / `source_url` / `created_at` 视为只读字段，不会被修改；
- 可更新：`tags` / `category` / `priority`；
- `--regen {title,summary,tags,all}`：根据当前 Markdown 正文调用生成器重新计算部分字段；
  - `embedding` 目前不会在 `update` 中执行向量重算；如需重建向量，请使用 `clawsqlite knowledge embed-from-summary`。
- 更新后自动同步 FTS/vec 索引。

示例：

```bash
# 手工修 tags
clawsqlite knowledge update --id 3 --tags "rag,sqlite,openclaw"

# 用启发式重新生成摘要
clawsqlite knowledge update --id 3 --regen summary --gen-provider openclaw

# 用小模型生成/重刷标签
clawsqlite knowledge update --id 3 --regen all --gen-provider llm
```

### 6.5 delete

```bash
clawsqlite knowledge delete --id ID [--hard] [--remove-file]
```

语义：

- 默认：软删
  - 标记 `deleted_at`；
  - 从 FTS/vec 中移除索引；
  - Markdown 文件会重命名为 `.bak_deleted_<timestamp>`，方便后悔恢复；
- `--hard`：硬删
  - 从 `articles` 表删除记录；
  - 默认把正文文件改名为 `.bak_deleted_<timestamp>`；
  - 若同时指定 `--remove-file`，则直接物理删除 Markdown 文件。

配合 `maintenance prune` 可以在一段时间后批量清理这些备份文件。

### 6.6 reindex

```bash
clawsqlite knowledge reindex --check
clawsqlite knowledge reindex --fix-missing --gen-provider openclaw
clawsqlite knowledge reindex --rebuild --fts
clawsqlite knowledge reindex --rebuild --vec
```

用途：

- `--check`：检查缺失字段和索引状态；
- `--fix-missing`：利用当前生成器补齐缺失的摘要/标签/FTS/vec 行；
- `--rebuild`：重建索引：
  - `--fts`：通过 plumbing (`clawsqlite index rebuild`) 重建 FTS 索引；
  - `--vec`：只“清空” vec 表（不重算 Embedding），后续需配合
    `clawsqlite knowledge embed-from-summary` 等命令重新填充向量。

### 6.7 maintenance

```bash
clawsqlite knowledge maintenance prune --days 3 --dry-run
```

主要功能：

- 扫描 Markdown 目录，找出：
  - 不再被 DB 引用的孤儿文件；
  - 命名形式为 `.bak_*` 的备份文件；
  - DB 中记录的 `local_file_path` 指向不存在文件的“断链记录”。
- `--dry-run`：只输出报告，不做删除；
- `--days`：备份保留天数（默认 3 天，`--days 0` 表示不保留）。

这一套配合软删/硬删的备份策略，实现“删除先备份，过保留期后再真正清理”的两阶段删除。

---

## 7. 文件命名与中文标题

Markdown 文件命名形式：

```text
<id>__<slug>.md
```

- `id` 来自数据库自增主键；
- `slug` 由标题生成，规则：
  - 使用 Unicode 规范化（NFKC）；
  - 若安装了 `pypinyin`，中文片段会转为拼音；
  - 保留 ASCII 字母/数字，其他符号统一替换为 `-`；
  - 连续多个 `-` 合并，去掉首尾 `-`；
  - 为空时回退为 `untitled`。

因此对于中文标题，通常会得到拼音 slug；如果未安装 `pypinyin`，可能回退为
`untitled`。例如：

> 标题：`深入理解 OpenClaw 多 Agent 架构`
>
> 文件名示例：`<id>__shen-ru-li-jie-openclaw-duo-agent-jia-gou.md`

---

## 8. 备注

- 当前版本主要面向“新建知识库”的使用场景，不负责自动迁移旧版 schema；
- 对 URL 和 ID 的约束比较严格：
  - `source_url` 对外部 URL 唯一；
  - `ingest` 默认拒绝重复 URL，只有在显式 `--update-existing` 时才允许刷新；
- 删除逻辑采用“先备份后清理”的两阶段方案；
- 针对中文内容：
  - 在有 `jieba` 依赖的环境下，标签生成优先使用 `jieba.analyse.textrank`；
  - 在启发式路径中，对“字数/阅读时间”等常见噪音有额外过滤；
  - 在生成 summary/tags 前，会尝试剥离 Markdown 的 metadata 区块和公众号式阅读统计。

如果你有新的使用场景或改进建议，欢迎在 GitHub 仓库
（`ernestyu/clawsqlite`）中提交 issue 或 PR。

---

## License

MIT © Ernest Yu
