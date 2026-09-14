# PDF 切分质量缺陷：甘特图/表格被打散、重复目录页去重错位、加粗行误判为标题

2026-09-14 用真实文件 `ch05.pdf`（70 页讲稿，`sha256=5b3927ea56e6b4ef9cc3ebf09497041fa380e1db21970012d590bc1a8616cd17`）对开发环境已入库的 chunk 做逐页比对，发现切分质量在**表格/甘特图页**与**重复目录页**两类内容上明显劣化，并存在真实内容丢失。

被比对对象（开发栈 `rag-mvp`）：

| 项 | 值 |
|---|---|
| Document ID | `01a09b0c-592e-7f0e-9a1d-594f12355b49` |
| dataset | `a2379aa8-4b74-5490-9e88-3e8a7a946703`（name=`111`） |
| index_version / 状态 | `1` / `IndexBuild=ACTIVE`，`active_version=1` |
| source_name | `ch05.pdf` |
| 落库 chunk | 296（`rag.chunk_manifests` 与 ES `rag-chunks-v1*` 双向一致，无差集） |

## 比对方法

1. `pypdf` 逐页提取原始文本作为 ground truth（70 页）。
2. 从开发栈取出全部 296 条 chunk 与解析器产出的 332 个 `ParsedSegment`。
3. 三种口径交叉验证：**8-gram 连续片段保留率**（短语是否原样保留，对重排敏感、偏严）、**token 级覆盖率**（内容是否真的进入索引，对乱序宽容、偏松）、**逐页逐行归属**（哪一页丢了哪一行）。
4. 用开发栈 Elasticsearch 的 BM25 通道做真实召回，确认缺陷块是否进入检索结果。

## 量化基线

| 指标 | 值 | 解读 |
|---|---|---|
| 正文 token（剔除导航栏与页脚后） | 4752 | ground truth |
| 切块草稿 → 落库 chunk | 332 → 296 | 36 个因 `chunk_id` 去重被折叠 |
| chunk 长度 | 中位 **88** 字符 / 最大 612 | 40 块 <40 字符、138 块 <80 字符；**无一块接近 `chunk_size=800`** |
| 8-gram 保留率 | 解析器 **80.6%** → 落库 **74.7%** | 丢失主要发生在解析阶段，切块阶段仅再降约 6 个点 |
| token 级内容保留 | 全库 **97.45%**（找不到 2.55%） | 真正未进入索引的内容不多但存在 |
| 本页可见性 | **80.7%** | 19.3% 的正文 token 在其所属页的 chunk 中找不到（去重 + 表格丢失） |
| 普通单栏文本页 | 8-gram recall 0.90–0.97 | 常规正文页质量良好 |

结论：**缺陷高度集中在表格/甘特图页（p37/46/48/49/51/52/32/36）与 8 个重复目录页（p6/9/12/38/53/60/62/67）**，且主要根因在解析器而非 `chunk_size/chunk_overlap`。

## 缺陷 1（P0）：甘特图与队列表被打散成乱序字母数字流，并造成真实内容丢失

p48 原始表格：

```text
1 2 3 ... 30
Q2 B B B B B B
Q1
Q0 A A A A A A
```

落库 chunk（`ordinal 218`，p48）正文：

```text
…Better Accounting (contd.)
1 Q 2 Q 1 Q 0 28AB121314151617181920222330BA35810A2BBBAAA4679112124B25262729 28BB12131516171819
```

同类形态：`ordinal 160/161`（p37）、`ordinal 135`（p32）、`ordinal 203/205`（p46）、`ordinal 94`（p24）。后果：

- 行标 `Q0/Q1/Q2` 与数值错位，列对齐信息全部丢失；
- 甘特图刻度与表头 token **全库都找不到**：`16 17 22 23 24 25 26 27 28 29 31 32`、`q0`、`ab`、`cpu0–cpu3`、`tturnaround`、`twaiting` 等。逐页统计：p46 丢 28/123 token、p48 丢 22/108、p49 丢 12/63、p15 丢 11/61、p65 丢 8/81；
- 检索直接命中这些碎片：查询 `Key to Q1 FCFS SJF PRIO RR turnaround waiting` 的 top1 为 `ordinal 157`（正文仅 `1 2 3 4 5 3 4 5 3 5 3 / Q2: / FCFS SJF PRIO RR`），top2 为 `ordinal 160`（`PPPPPPPPPPPPPPPP4510121416171920PPPPP2PP3678…`）。

根因链路（`src/rag_mvp/adapters/parsers/pdf.py`）：

1. `_group_fragments`（`pdf.py:409-421`）按 `x` 间距 `> max(18pt, 2.5×字号)` 在行内插入 `\t` 作为列分隔；
2. `_table_line_orders`（`pdf.py:649-661`）把“连续 ≥2 行且列数 ≥2”判为表格；
3. `_block_text`（`pdf.py:668-673`）按 `\t` 拆列并拼成 Markdown `| a | b |`。

甘特图网格每行碎片数不同、列 `x` 位置不逐列对齐，于是列被拆错位并重排，拼出乱序流。

## 缺陷 2（P1）：完全重复的目录页被去重，`locator` 只保留首次出现页

p5/p6/p9/p12/p38/p53/p60/p62/p67 是内容完全相同的 Contents 页。`chunk_id = xxh64(content_with_weight + document_id)` 不含位置，`IngestionPipeline` 按首次出现折叠（`src/rag_mvp/ingestion/pipeline.py:65-75`）：

| 页 | 该页 chunk 数 | 本页 token 覆盖率 | 全库覆盖率 |
|---|---|---|---|
| 5 | 2（`ord 10/11`） | 57% | 58% |
| 6/9/12/38/53/60/62/67 | **0** | **0%** | 58% |

影响：检索命中目录内容时只引用 **page 5**，用户翻到 page 6 无法对应；同时目录条目被拆坏——`ord 10` 正文为 `Contents` + `1 2 … 9`，`ord 11` 正文为 `Contents` + `Warm-upBasic ConceptsScheduling Criteria…`，**编号与条目标题分属两块**，`"1 Warm-up"` 作为短语在库中不存在（这也是重复页全库覆盖率只有 58% 的原因）。

## 缺陷 3（P0）：加粗行一律被当作标题，正文归属错乱

`heading_path` 中出现 `A`(9 次)、`CPU`(4)、`RR`(3)、`P`(2)、`Q`(2)、`SJF`(1) 等短标题：p37 的表格行首标记被升级为小节（该页 6 个 chunk 的前缀均为 `In Class Exercise > FCFS|SJF|PRIO|RR`），p20 出现 `Simple Scheduling Algorithms > A`。

根因：`_heading_level`（`pdf.py:637-646`）末条 `if ratio >= 1.12 or line.bold: return 4`，任何加粗行都成为四级标题。标题文本只进入后续块的 `heading_path` 前缀、不作为正文块发出，因此讲稿中加粗的表头、关键词与选项字母会造成正文归属与层级错误。

## 缺陷 4（P1）：块过碎，单块信息量低

`ordinal 6` = `Objectives` + `systems`（19 字符）、`ordinal 19` = `Basic Concepts` + `Dispatcher`、`ordinal 153` = `In Class Exercise > Key to` + `Q1:` + `1`、`ordinal 234` = `…Key`。原因是解析器按 layout block 产出 segment，讲稿型 PDF 每页仅数行，`chunk_size=800/overlap=120` 几乎不生效。**调切块参数无法解决这份 PDF 的问题**。

## 缺陷 5（P2）：页脚漏网与表格误判

- 5 个 chunk 正文仍含页脚（如 `ordinal 2/94/152/161/272`：`A/Prof. Kai Dong Operating System Concepts Chapter 5. CPU Scheduling 24 / 70`）。导航栏 70 行、页脚 68 行整体被正确剔除，属漏网。
- `ordinal 73`（p20）把项目符号文本判成表格，输出 `| processesP | -Parrived at time 0. |`，而真正的表格行 `P1 10 3 / P2 1 1 / …` 大量缺失（p20 本页 token 覆盖 74%、全库 79%）。

## 影响面

- **检索链路本身正常**（`priority boost every 10 ms` 正确命中 p46 `ordinal 204`），问题在于被检索单元的质量：命中甘特图页时交给模型的是乱序碎片，命中目录时页码指向 p5。
- 受影响内容集中在 MLFQ/调度算法章节的甘特图与表格页，以及 8 个目录页；普通正文页暂未发现明显劣化。
- 当前生产栈运行 GHCR 的 `5ae3241`，同一份 PDF 若在生产重建会得到相同结果；本缺陷与 Agentic RAG Loop 迭代无关，属解析/切块层。

## 建议修复顺序

| 优先级 | 缺陷 | 方向 |
|---|---|---|
| P0 | 加粗行当标题（缺陷 3） | 收紧 `_heading_level`：`bold` 仅在“行短 + 无元素符号/无多列 + 与正文存在行距”时判为标题 |
| P0 | 甘特图/表格乱序与丢失（缺陷 1） | 表格块先按列的 `x` 区间聚类再逐行对齐输出；无法恢复结构时保留原始物理行顺序，禁止把数字与字母打散重排 |
| P1 | 重复页去重的定位（缺陷 2） | 保持 `chunk_id` 规则不变（与 RAGFlow 对齐），在 chunk `metadata` 记录出现页列表并在 Evidence 中返回；同时修复目录页“编号与条目”分段分离 |
| P1 | 块过碎（缺陷 4） | 为 PDF 增加同页、同 `heading_path` 下相邻小段落的合并策略 |
| P2 | 页脚漏网与表格误判（缺陷 5） | 页脚判定放宽到“页边行含页码形态即可删除”；表格判定要求整体列 `x` 位置对齐 |

任何改变 `content_with_weight` 的修复都必须提升 `parser_version`（当前 `source-router-v7`，见 `src/rag_mvp/config.py:86`），否则 `chunk_id` 与既有索引语义会失配。

## 复现命令

```bash
# 1. 文档与 chunk 清单（MySQL）
docker exec rag-mvp-mysql-1 mysql -uroot -proot-rag-dev -t -e \
 "SELECT ordinal, chunk_id, locator, metadata_json FROM rag.chunk_manifests
  WHERE document_id='01a09b0c-592e-7f0e-9a1d-594f12355b49' ORDER BY ordinal;"

# 2. 落库 chunk 正文（Elasticsearch）
docker exec rag-mvp-elasticsearch-1 sh -c \
 'pw=$(cat /usr/share/elasticsearch/config/search-guard/rag_mvp_password);
  curl -s -k --cacert /usr/share/elasticsearch/config/search-guard/ca.pem -u "rag_mvp:$pw" \
   "https://localhost:9200/rag-chunks-v1*/_search" -H "Content-Type: application/json" \
   -d "{\"size\":500,\"query\":{\"bool\":{\"filter\":[{\"term\":{\"document_id\":\"01a09b0c-592e-7f0e-9a1d-594f12355b49\"}}]}},\"sort\":[{\"ordinal\":\"asc\"}]}"'

# 3. 复现缺陷 1 的检索噪声（BM25，无需模型）
#    查询 "Key to Q1 FCFS SJF PRIO RR turnaround waiting"，观察 top 命中是否为
#    ordinal 157/160 这类无结构碎片。
```

比对脚本要点：`pypdf` 逐页取文本 → 去掉导航栏（`^0\.Prologue.*8\.RT Sche\.$`）与页脚（`^A/Prof\.Kai Dong … \d+ / 70$`）→ 计算 8-gram 连续片段保留率与 token 覆盖率 → 与 `PdfParser` 的 segment 输出和落库 chunk 分别对照。

## 验证状态（初始报告，修复前）

本文档仅为**只读比对结论**，未修改任何解析/切块代码，未新增或运行测试。上述数字来自开发栈 `rag-mvp` 的真实 MySQL 与 Elasticsearch 数据，以及本地 `pypdf` 对原始 PDF 的提取；BM25 查询为只读 `_search` 调用。修复后需要重新摄取该 PDF 并复跑本文的量化基线，作为回归依据。

## 修复进展（2026-09-14）

进一步用原文件检查坐标发现：`pypdf` 的 visitor 在部分文本对象/变换矩阵切换后返回零文本矩阵，目录条目和甘特图字符被错误放到页底；原代码还按未缩放字号估算边界。这比列间距阈值更上游，单改阈值不足以恢复内容。

本次实现：

- `auto/deepdoc` 用 pdfminer.six 实际字符坐标归组物理行，处理缩放和下标；`plain`、原生文字不足时的 OCR 路由保持原入口。
- 加粗不再单独判标题；短大写标签、项目符号和表格行保留在正文。页首标题从根层级开始，避免重复 Contents 继承上一页标题。
- 只有连续行列数、列起点和行距均匹配时输出 Markdown 表格；稀疏图表保留物理行，不猜测未绘制的单元格。p20 的 P1–P5 表已恢复；p48 的刻度 1–30 和 Q0/Q1/Q2 已恢复。
- 同页同标题下的段落/列表合并，表格保持独立边界；已有 chunk_size/overlap 仍限制长块。
- PDF 逻辑 chunk 去重后在 `metadata.page_numbers` 返回升序唯一页列表（JSON 字符串）；主 locator/bbox/ordinal 仍保留第一次出现。既有 Evidence/protobuf metadata map 原样传递，无需变更 proto。
- parser 默认版本及 Compose、RPC、环境示例同步提升为 `source-router-v8`，旧文档需要重建才能使用修复。

### 本地真实文件前后对比

使用同一 SHA-256 的原文件、HEAD 版本旧解析器与修复后解析器、`chunk_size=800/overlap=120` 做本地只读对比。以下是**重新定义后统一重跑的口径**，不与上文原报告不同口径的 8-gram 数字混用：原始 pypdf 文本删除导航栏与页脚；token 为小写 ASCII 字母数字连续串或单个汉字；8-gram 为拼接这些 token 后的连续 8 字符。覆盖率按原文出现次数加权、检查目标中是否存在；本页口径在新版使用重复页列表。该口径不验证数值单元格的视觉列对应，也不等同于检索质量指标。

| 指标 | 旧实现重跑 | 修复后本地输出 |
|---|---:|---:|
| ParsedSegment / 草稿 / 唯一 chunk | 332 / 332 / 296 | 82 / 82 / 74 |
| 唯一 chunk 字符长度中位数 | 87.5 | 325 |
| 原文正文 token 数 | 4834 | 4834 |
| 全库 token 存在率 | 97.52% | 98.43% |
| 本页 token 可见率 | 80.64% | 98.06% |
| 规范化连续 8 字符存在率 | 83.83% | 97.92% |
| 重复 Contents 出现页 | 仅首次主定位 | 5、6、9、12、38、53、60、62、67 |

这些数字来自本地解析/切块，**尚未对开发或生产文档执行重摄取，也未复跑新索引的 BM25/真实模型召回**。仍有少量 token 不匹配，不能宣称完整恢复所有公式或图表语义。

### 自动化验证

- 新增自生成 PDF 回归：缩放目录与重复页、网格下标、加粗正文、小段合并、单页页脚、错位列和项目符号表格误判；Pipeline 回归验证 page_numbers 的 Evidence/protobuf 透传和重执行幂等。已观察到修复前失败、修复后通过。
- `make ci`：离线门禁已通过（包括 lint、类型检查、生成物、unit/contract/functional、Fake resilience、离线 eval 与核心覆盖率）。
- `make docker-test SUITE=integration`：32 passed、4 skipped、3 failed、4 errors；失败项为两个真实 Embedding 测试、Search Guard 安全连接 ping 断言及四个 E2E 初始化。模型测试缺少 `EMBEDDING_MODEL_NAME/DIMENSION`，其中空维度触发 Settings 校验失败。PDF OCR 用例未失败。此次不能算完整集成验收通过，未修改这些环境/安全配置来绕过测试。
- 未运行 `SUITE=resilience|eval|all`，未执行真实 ch05.pdf 的 gRPC 重摄取及新索引检索验收，未提交或推送代码。
