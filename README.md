# audit-enterprise-knowledge 知识库缺口体检

给 AI 一个本地资料文件夹和/或客户官网地址，它会出一个网页报告，告诉你：

- 知识库七个模块（公司概述、产品介绍、解决方案、合作案例、行业知识、FAQ、其他）下的 34 个小板块，每个是 **有 / 薄弱 / 缺**，现状是什么，怎么补；
- 三本待办台账：**CUS** 要问客户的，**MAT** 资料要处理的，**ANM** 内部要判断的风险；
- 每个文件**能不能自动判断**。扫描版 PDF 和图片会先在本机 OCR，OCR 也读不出的才算“不能”，原因写进 MAT。

这是一个 AI 技能（Skill），适用于 Claude Code、Codex 等支持 `SKILL.md` 的 AI 工具。

## 安装

把这个仓库放进 AI 工具的 skills 目录，文件夹名保持 `audit-enterprise-knowledge`。

**Claude Code**

```bash
git clone https://github.com/anaisyli/audit-enterprise-knowledge.git ~/.claude/skills/audit-enterprise-knowledge
```

**Codex**

```bash
git clone https://github.com/anaisyli/audit-enterprise-knowledge.git ~/.codex/skills/audit-enterprise-knowledge
```

其他工具放进各自的 skills 目录即可。装好后重新打开 AI 工具。

**依赖**

- Python 3.10 以上。脚本只用标准库就能跑。
- 读 PDF、扫描件 OCR：`python -m pip install pymupdf`
- OCR 引擎有一个就行，全部在本机识别，不上传：
  - Windows 10/11 自带 OCR，中文系统一般不用另装；没有中文识别时，在“设置 → 时间和语言 → 语言”里给中文加上“光学字符识别”；
  - 或者 `python -m pip install rapidocr_onnxruntime`（跨平台，中文更准）；
  - 或者 Tesseract。
- 读 `.doc / .xls / .ppt` 旧格式：装 LibreOffice（装在别处时设置环境变量 `LIBREOFFICE_PATH`）。

缺了哪样都能跑，只是对应的文件会记进 MAT，写明原因。

## 怎么用

直接跟 AI 说，下面这些说法都可以：

**本地资料 + 官网**

```text
用 audit-enterprise-knowledge 看一下 D:\客户资料\示例公司 和 https://www.example.com ，出一份缺口报告。
```

**只看官网**

```text
帮我看看 https://www.example.com 做知识库还缺什么，出个网页报告。
```

**只看本地资料**

```text
D:\客户资料\示例公司 这批资料能建成什么样的知识库？哪些文件读不了？
```

**几个文件夹一起看**

```text
把 D:\客户资料\画册、D:\客户资料\邮件附件 和官网 https://www.example.com 一起做个知识库体检。
```

**告诉它行业和公司名，判断更准**

```text
这是一家贸易公司，公司叫“示例公司”，参数要看型号、材质、公差。资料在 D:\客户资料\示例公司，官网 https://www.example.com ，做个缺口分析。
```

**只要给客户的问题**

```text
按 audit-enterprise-knowledge 的结果，把 CUS 里要问客户的问题整理成一张清单，我要发给客户。
```

**资料很多 / 不想做 OCR**

```text
资料里扫描件和图片很多，OCR 最多识别 800 页。
这次不用做 OCR，先快速看一下。
```

**官网读不到正文时**（JS 页面、被拦截）

```text
官网是 JS 页面，我把页面存在 D:\官网保存 了，用这些页面做体检。
```

保存的页面可以是浏览器另存的 `.html`，也可以是 `.txt`（第一行写 `URL: 网址`）。

## AI 会怎么做

1. **跑脚本**：列出每个文件并读出文字，扫描版 PDF 和图片先在本机 OCR；读官网（默认最多 80 页，遵守 robots.txt，只读给定的域名）；按关键词把段落分到 34 个小板块做初判；找出候选问题，包括：
   - 写法不一致的公司数据（成立年份、仓库数、出口国家数等）；
   - 工厂还是贸易商的说法；
   - 案例页；
   - 别家品牌和授权说法；
   - 官网套模板没改、重复页；
   - 要清理的价格和联系方式。
2. **读原文判断**：关键词会误命中，所以 AI 读原文后逐个小板块定状态（改了初判要写原因），确认或剔除候选问题，补充脚本没发现的问题。
3. **出网页**：脚本先检查判断有没有写全、来源编号对不对、有没有写进金额和邮箱，通过后生成 `report.html`。宿主能发布网页时（比如 Claude 的 Artifact），可以发布成私有页面。

## 报告里有什么

| 部分 | 内容 |
|---|---|
| 开头 | 检查对象、日期、来源、无法自动判断的份数；有/薄弱/缺的总数和各模块条形图 |
| 七个模块 | 每个小板块的状态、现状、怎么补、引用的资料编号；可以只看“缺”或“薄弱” |
| CUS 待客户补充事项 | 公司数据以哪个为准、案例能否公开、证书能否展示等 |
| MAT 源资料处理台账 | OCR 文字待核对、OCR 也读不出的图片和扫描页、视频没转写、压缩包没解开、报价单要清理、官网没读完 |
| ANM 源文与事实异常台账 | 别家品牌和授权说法、检测报告页、参考稿、官网复制粘贴错误和重复页 |
| 需清理的内容 | 每份资料里有几处价格、付款条件、邮箱、电话（只计数，不显示原文） |
| 建议先补的事 | 3–7 项 |
| 资料清单 | 每个文件和网页：类型、能否自动判断（能 / 部分 / 不能）、原因 |

## 哪些文件能自动判断

| 文件 | 情况 |
|---|---|
| Word（docx）、PPT（pptx）、Excel（xlsx）、txt、md、csv、json、html | 直接读文字 |
| PDF | 有文字层的页直接读；没有文字层的页先 OCR，读出来就算“能”，同时在 MAT 提醒 OCR 文字待核对；OCR 也读不出的页记为“部分”或“不能” |
| 图片 | 先 OCR：读出成段文字（证书、参数表、画册页照片）就算“能”；没读出文字的（多半是产品、现场照片）记为“不能”，要人看 |
| doc、xls、ppt、wps 等旧格式 | 装了 LibreOffice 会自动转换后读，没装记为“不能” |
| 像报价单或订单的 Excel | “部分”：能读，但价格和买家信息要清理，不能直接当公开知识 |
| 视频、音频 | “不能”：没有转写 |
| 压缩包 | “不能”：没有解开 |
| ai、psd、cdr、dwg 等设计文件 | “不能”：要人打开看 |

## 它在整个流程里的位置

```text
客户资料 + 官网
  → audit-enterprise-knowledge         本 Skill：建库前体检，出缺口报告
  → prepare-seo-knowledge-materials    筛选资料，做初筛包
  → compile-enterprise-knowledge       正式建库，按原文重新算缺口
```

七个模块、小板块和 CUS/MAT/ANM 分工和 compile-enterprise-knowledge 一样，体检结论可以直接对照以后的知识库。不装另外两个 Skill 也能单独用；体检不要求先筛选资料，也不改任何资料。

## 手动运行

不经过 AI，也可以直接跑脚本：

```bash
python scripts/run_audit.py --files "<资料文件夹>" --site https://www.example.com --out "<运行目录>"
```

然后由人或 AI 按 `references/review-rules.md` 填写 `<运行目录>/review.json`，再生成网页：

```bash
python scripts/render_report.py --out "<运行目录>"
```

常用参数：

| 参数 | 作用 |
|---|---|
| `--files` | 一个或几个本地文件夹或文件 |
| `--site` | 官网地址，可以几个 |
| `--capture` | 浏览器保存的官网页面文件夹 |
| `--out` | 运行目录，放在资料文件夹外面 |
| `--config` | `audit-config.json`：公司名、行业、行业关键词 |
| `--max-pages` | 官网最多读几页，默认 80 |
| `--ocr-max-pages` | 一次最多 OCR 几页/张，默认 300 |
| `--no-ocr` | 不做 OCR |

`audit-config.json` 示例：

```json
{"company": "示例公司", "industry": "<客户所在行业>", "project_id": "P-001",
 "own_terms": ["示例公司", "EXAMPLE"],
 "extra_keywords": {"product/参数": ["<型号词>", "<材质词>"], "industry/技术知识": ["<工艺词>"]}}
```

## 仓库结构

```text
SKILL.md                    给 AI 看的流程说明
references/review-rules.md  判断规则和 review.json 写法
scripts/                    读文件、读官网、OCR、初判、生成网页
assets/                     小板块关键词表、报告样式
tests/                      测试
```

## 已知限制

- 分小板块靠关键词，会误命中，所以脚本只做初判，状态由 AI 读原文后定。
- OCR 只做没有文字层的 PDF 页和图片文件；只有图片的 PPT 页、Word 里嵌的图片不做。本机 OCR 不如 MinerU 准，表格和多栏画册容易错位，读出的内容只用于体检判断。
- OCR 一次合计最多 300 页/张，超出的登记在 MAT；文件名像证书、报告、画册页的优先识别。
- 不转写视频。
- 公司数据只自动检查成立年份、员工数、面积、仓库数、出口国家数、经验年数、库存量、生产线数；名单类不一致（合作厂商、认证列表）靠 AI 读原文发现。
- 报告是内部材料，有资料相对路径和原文摘录，不要直接发给客户；要给客户的只有 CUS 那部分。

## 测试

```bash
python -m unittest discover -s tests
```

测试不联网。没有 PyMuPDF 或 OCR 引擎的电脑会自动跳过 OCR 测试。
