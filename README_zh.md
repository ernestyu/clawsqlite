# Clawkb

一个为 [OpenClaw](https://github.com/openclaw/openclaw) 设计的 **本地 Markdown + SQLite 知识库工具**，人和 Agent 都可以用。

Clawkb 主要帮你解决两件事：

- 把网页和零散笔记统一存成 Markdown 文件 + SQLite 记录，放在一个可控的根目录里；
- 提供快速的全文检索和可选的向量检索，让你和 Agent 都能稳定地查到这些内容。

核心能力包括：

- 将 URL 或原始文本入库为 Markdown 文件 + 数据库记录
- 基于 FTS5 的全文检索
- 可选的向量检索（依赖外部 Embedding 服务）
- 基于启发式或小模型的小结（title / summary / tags）生成与刷新
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
  - 所有数据放在同一个根目录下：`CLAWKB_ROOT`
  - 数据库默认：`<root>/clawkb.sqlite3`
  - Markdown 目录默认：`<root>/articles`

- **可选 Embedding 和小模型**
  - Embedding：通过兼容 OpenAI 的 `/v1/embeddings` HTTP 接口
  - 小模型（small LLM）：通过兼容 `/v1/chat/completions` 的接口生成标题/摘要/标签

- **命令行优先**
  - 核心子命令：`ingest`，`search`，`show`，`export`，`update`，`delete`，`reindex`，`maintenance`

---

## 2. 运行环境

Clawkb 设计时默认运行在 OpenClaw 容器内，但也可以在普通 Linux 环境中独立使用。

必要条件：

- Python 3.10+
- SQLite 内置 FTS5 支持

Python 依赖：

- `jieba`（可选但强烈推荐，用于中文标签和关键词抽取）

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

如果缺少这些扩展，Clawkb 会自动降级：

- FTS：使用 SQLite 内建 tokenizer；
- 向量检索：在 vec0 不可用时自动关闭，仅使用 FTS。

---

## 3. 安装与启动

克隆本仓库：

```bash
git clone git@github.com:ernestyu/Clawkb.git
cd Clawkb
```

在 OpenClaw 工作目录中，这个仓库通常位于：

```text
/home/node/.openclaw/workspace/Clawkb
```

有两种方式运行 Clawkb：

```bash
python -m clawkb --help
# 或者
./bin/clawkb --help
```

`bin/clawkb` 是一个简单包装脚本，方便从仓库根目录执行 `python -m clawkb`。

---

## 4. 配置

### 4.1 根目录与路径

Clawkb 的根目录、数据库路径、Markdown 目录均可通过 CLI 参数或环境变量配置，优先级如下：

**根目录 (`CLAWKB_ROOT`)：**

1. CLI 参数：`--root`
2. 环境变量：`CLAWKB_ROOT`
3. 环境变量：`CLAWKB_ROOT_DEFAULT`
4. 默认：`<当前工作目录>/clawkb_data`

**数据库路径 (`CLAWKB_DB`)：**

- `--db` > `CLAWKB_DB` > `<root>/clawkb.sqlite3`

**Markdown 目录 (`CLAWKB_ARTICLES_DIR`)：**

- `--articles-dir` > `CLAWKB_ARTICLES_DIR` > `<root>/articles`

### 4.2 项目级 `.env`

Clawkb 推荐使用项目根目录下的 `.env` 文件集中配置。可以先从例子复制：

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
CLAWKB_VEC_DIM=1024

# 小模型（可选，用于生成标题/摘要/标签）
SMALL_LLM_BASE_URL=https://llm.example.com/v1
SMALL_LLM_MODEL=your-small-llm
SMALL_LLM_API_KEY=sk-your-small-llm-key

# 根目录及路径覆盖（可选）
# CLAWKB_ROOT=/path/to/clawkb_root
# CLAWKB_DB=/path/to/clawkb.sqlite3
# CLAWKB_ARTICLES_DIR=/path/to/articles
```

运行时，`clawkb.__main__` 会在 CLI 解析前调用 `load_project_env()` 读取 `.env` 并填充 `os.environ`（不会覆盖系统已有变量）。

### 4.3 Embedding 配置

当你需要向量检索（`articles_vec`）时，需要配置 Embedding 服务：

必需环境变量：

- `EMBEDDING_MODEL`：服务端模型名
- `EMBEDDING_BASE_URL`：基础 URL，例如 `https://embed.example.com/v1`
- `EMBEDDING_API_KEY`：访问令牌
- `CLAWKB_VEC_DIM`：Embedding 维度（如 BAAI/bge-m3 为 1024）

Clawkb 会：

- 在 `clawkb.embed.get_embedding()` 中调用上述 HTTP 接口获取向量；
- 用 `CLAWKB_VEC_DIM` 创建 `articles_vec` 表的 `embedding float[DIM]` 列；
- 在 `embedding_enabled()` 为假或 vec 表不存在时自动降级为纯 FTS 模式。

如果上述任一变量缺失：

- `embedding_enabled()` 返回 `False`；
- `ingest` 不会请求 Embedding，也不会写 `articles_vec`；
- `search` 的 `mode=hybrid` 会自动退化为 FTS 逻辑。

### 4.4 小模型（Small LLM）配置

如果希望用小模型生成/优化标题、摘要和标签，可以配置：

```env
SMALL_LLM_BASE_URL=https://llm.example.com/v1
SMALL_LLM_MODEL=your-small-llm
SMALL_LLM_API_KEY=sk-your-small-llm-key
```

然后在入库或更新时使用：

```bash
--gen-provider llm
```

Clawkb 会调用兼容 OpenAI 的 chat completions 接口，请求小模型输出一个 JSON，里面包含 `title`、`summary` 和 `tags` 三个字段。

如果未配置 SMALL_LLM，则：

- `gen-provider=openclaw` 会使用纯启发式；
- `gen-provider=llm` 会报错并提示 SMALL_LLM 未启用。

### 4.5 抓取脚本（Scraper）配置

Clawkb 本身不负责网页抓取；对于 `--url` 模式，会调用你提供的脚本。

配置方式：

- CLI：`--scrape-cmd`
- 环境变量：`CLAWKB_SCRAPE_CMD`

示例：

```env
CLAWKB_SCRAPE_CMD="node /path/to/scrape.js --some-flag"
```

行为说明：

- `.env` 中的值支持加引号，Clawkb 会在载入时去掉外层引号；
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

2. **兼容：旧格式**

   ```text
   Title: 文章标题
   # 一级标题
   正文……
   ```

Clawkb 会从这两种格式中解析出标题和 Markdown 正文。

---

## 5. 快速上手

### 5.1 最小配置

1. 克隆仓库并进入目录：

   ```bash
   git clone git@github.com:ernestyu/Clawkb.git
   cd Clawkb
   ```

2. 创建 `.env`（推荐）：

   ```bash
   cp ENV.example .env
   # 编辑 .env，至少设置 CLAWKB_ROOT，按需配置 EMBEDDING_* / SMALL_LLM_*
   ```

3. 第一次入库（纯文本）：

   ```bash
   python -m clawkb ingest \
     --text "你好，Clawkb" \
     --title "第一次笔记" \
     --category test \
     --tags demo \
     --gen-provider off \
     --json
   ```

   这一步会：

   - 创建 `<root>/clawkb.sqlite3`；
   - 在 `<root>/articles` 下生成 `000001__第一次笔记.md`（实际文件名取决于 slug）；
   - 写入 FTS 索引（如果 Embedding 已配置且 vec 表可用，会一起写入向量索引）。

4. 搜索刚才那条记录：

   ```bash
   python -m clawkb search "Clawkb" --mode fts --json
   ```

### 5.2 从 URL 入库

假设 `.env` 中已经配置了抓取脚本：

```bash
python -m clawkb ingest \
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
python -m clawkb ingest \
  --url "https://example.com/article" \
  --gen-provider openclaw \
  --update-existing \
  --json
```

语义：

- 若数据库中存在同一 `source_url` 的记录（包括软删记录）：
  - 复用原来的 `id`；
  - 更新 `title` / `summary` / `tags` / `category` / `priority`；
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
python -m clawkb <command> --help
```

查看详细说明。

### 6.1 ingest

```bash
python -m clawkb ingest --url URL [选项]
python -m clawkb ingest --text TEXT [选项]
```

常用参数：

- `--url` / `--text`：二选一
- `--title` / `--summary` / `--tags` / `--category` / `--priority`
- `--gen-provider {openclaw,llm,off}`：字段生成方式
- `--max-summary-chars`：摘要最大长度
- `--scrape-cmd`：抓取命令（或用 `CLAWKB_SCRAPE_CMD`）
- `--update-existing`：URL 已存在时更新同一条记录

### 6.2 search

```bash
python -m clawkb search "查询词" --mode hybrid --topk 20 --json
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
python -m clawkb show --id 12 --full --json
python -m clawkb export --id 12 --format md --out /tmp/article.md
```

### 6.4 update

```bash
python -m clawkb update --id ID [补丁参数] [--regen ...]
```

行为：

- `id` / `source_url` / `created_at` 视为只读字段，不会被修改；
- 可更新：`title` / `summary` / `tags` / `category` / `priority`；
- `--regen {title,summary,tags,embedding,all}`：根据当前 Markdown 正文调用生成器重新计算部分字段；
- 更新后自动同步 FTS/vec 索引。

示例：

```bash
# 手工修 tags
python -m clawkb update --id 3 --tags "rag,sqlite,openclaw"

# 用启发式重新生成摘要
python -m clawkb update --id 3 --regen summary --gen-provider openclaw

# 用小模型同时重刷标题/摘要/标签
python -m clawkb update --id 3 --regen all --gen-provider llm
```

### 6.5 delete

```bash
python -m clawkb delete --id ID [--hard] [--remove-file]
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
python -m clawkb reindex --check
python -m clawkb reindex --fix-missing --gen-provider openclaw
python -m clawkb reindex --rebuild --fts
python -m clawkb reindex --rebuild --vec
```

用途：

- `--check`：检查缺失字段和索引状态；
- `--fix-missing`：利用当前生成器补齐缺失的摘要/标签/FTS/vec 行；
- `--rebuild`：重建 FTS 或 vec 索引。

### 6.7 maintenance

```bash
python -m clawkb maintenance prune --days 3 --dry-run
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
  - 保留中文、英文、数字字符；
  - 其他符号统一替换为 `-`，连续多个 `-` 合并；
  - 去掉首尾 `-`，为空时回退为 `untitled`。

因此对于中文标题，会得到类似：

> 标题：`深入理解 OpenClaw 多 Agent 架构：让你的 AI 助手学会分身术`
>
> 文件名：`<id>__深入理解OpenClaw多Agent架构-让你的AI助手学会分身术.md`

这既保持了可读性，又避免了过于复杂或不可见字符导致的问题。

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

如果你有新的使用场景或改进建议，欢迎在 GitHub 仓库（`ernestyu/Clawkb`）中提交 issue 或 PR。

---

## License

MIT © Ernest Yu
