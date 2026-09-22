# abstrackr API 工具包

面向 [abstrackr](https://abstrackr.com) 的**非官方** Python 客户端与筛选管线。
abstrackr 是一款免费、基于网页的系统评价文献筛选工具。

abstrackr 没有公开的 API 文档，但它的网页应用构建在一套朴素的 `/api/*` JSON 接口之上。
本工具包把这套接口变成可脚本化的能力：导入题录、批量提交筛选判定、按排除原因打标签、
管理盲法与导出，并额外提供一条完整的**筛选管线**——用于跑 AI 辅助的题摘层与全文层筛选，
带中央校验器、可验证的平台回写与 PRISMA 账目核对。

**Python 3.9+ · 仅标准库 · 无第三方依赖。**

---

## 总览

工具包分两层。

| 层次 | 用途 |
|---|---|
| **API 客户端** | 只读优先的 abstrackr JSON API 客户端。每一条路由都对生产站点实测过，验证结果见 [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md)。 |
| **筛选管线**（`pipeline/`） | 围绕客户端的一套可复现流程：把去重后的题录工作簿分批 → 判定 → 中央校验 → 建 Hold 池 → 回写平台 → 读回校验 → PRISMA 账目核对。 |

## 目录结构

| 路径 | 内容 |
|---|---|
| `abstrackr_client.py` | 会话客户端：基于 Cookie 的登录、带保护的读写 |
| `abstrackr_screen.py` | 主驱动：`login`、`whoami`、`import-ris`、`fetch-citations`、`map`、`submit`、`unblind`、`reblind`、`mode`、`export` |
| `abstrackr_tag.py` | 按映射 CSV 批量打标签 |
| `abstrackr_jsonl_report.py` | 把整项目 JSONL 导出转成 PRISMA 计数、评审者活动、冲突与 Cohen's κ |
| `pipeline/` | 筛选管线（`s1`–`s11` 各阶段） |
| `prompts/` | 题摘层与全文层的筛选 prompt 模板 |
| `docs/` | 接口参考、平台行为说明、管线 SOP |
| `probes/` | 演示题录导入行为的小脚本 |

## 环境要求

Python 3.9 及以上。**无需任何第三方包**，全部使用标准库。

## 安装

```bash
git clone https://github.com/ReGMeIoN/abstrackr-api-toolkit.git
cd abstrackr-api-toolkit
```

## 凭据

凭据从本地 JSON 文件读取，该文件**已被 git 忽略**，且**永不打印**：

```json
{ "email": "bot@example.com", "password": "..." }
```

把文件放在与工具包同级的 `abstrackr/` 目录下；或用环境变量 `ABSTRACKR_HOME`
指向存放 `credentials.json` 与 `.session_cookies.txt` 的目录。
程序只会显示掩码后的邮箱，绝不回显密码。

## 快速开始

```bash
py=python                       # 或 python3

# 1. 登录（本地缓存 cookie）
$py abstrackr_client.py login

# 2. 导入题录文件（强烈建议用 CSV，原因见 docs/PITFALLS.md）
$py abstrackr_screen.py --project <id> import-ris citations.csv --execute

# 3. 建立 idx -> citation_id 映射，供自己的判定表使用
$py abstrackr_screen.py --project <id> fetch-citations
$py abstrackr_screen.py --project <id> map

# 4. 批量提交筛选判定（先 dry run 看清楚）
$py abstrackr_screen.py --project <id> submit --limit 5
$py abstrackr_screen.py --project <id> submit --execute

# 5. 解盲、导出，再把 JSONL 变成 PRISMA 计数与 κ
$py abstrackr_screen.py --project <id> unblind --reason-category "QC Audit" --duration 15
$py abstrackr_screen.py --project <id> export --report
$py abstrackr_screen.py --project <id> reblind
```

也可以设置环境变量 `ABSTRACKR_PROJECT=<id>`，省去每次传 `--project`。

### 双人双筛（两账号模式）

```bash
$py abstrackr_screen.py --as-bot login
$py abstrackr_screen.py --project <id> --as-bot submit --execute
# 人工评审者在网页端筛选（模式 Double、开启盲法）；
# 之后由 Leader 解盲并导出。JSONL 会同时包含两套判定，
# abstrackr_jsonl_report.py 可据此算出冲突清单与 Cohen's κ。
```

## JSONL 报告包含什么

```bash
$py abstrackr_jsonl_report.py project.jsonl --outdir report/
```

| 产物 | 内容 |
|---|---|
| `prisma_numbers.md` / `.csv` | 状态与最终判定计数 |
| `reviewer_activity.csv` | 每位评审者的判定数、纳入数、排除数与排除率 |
| `agreement.csv` | 两两观察一致率、期望一致率与 Cohen's κ |
| `conflicts.csv` | 仍处于冲突状态的题录 |
| `included.csv`、`excluded.csv`、`maybe.csv`、`unscreened.csv` | 按状态分列的导出 |

## 筛选管线

`abstrackr_screen.py` 只管搬运题录；`pipeline/` 负责跑完整个综述。管线把去重后的
工作簿依次经过：分批 → 判定 → 中央校验 → **仅定义性归一** → Hold 池 → 可复现抽样 →
带保护的平台回写 → 读回校验 → PRISMA 账目 → 队列导出：

```
工作簿 → s1 分批    → 判定          → s2 校验 → s3 合并
       → s4 归一    → s5 Hold 池    → s6 抽样自审
       → s7 回写    → s8 读回校验   → s9 PRISMA
       → s10 队列导出                → s11 释放判定供人工复核
```

所有与具体综述相关的设定——判定编码、受控词表、条件式输出 schema、工作簿列名、
平台标签计划——全部集中在一个 `protocol.json` 里。阶段代码保持通用：换一个综述，
换一份 protocol 文件即可，不需要 fork 整条管线。

入门请看 [`pipeline/README.md`](pipeline/README.md)；方法与每条规则背后的理由见
[`docs/PIPELINE.md`](docs/PIPELINE.md)。

## 文档

| 文档 | 内容 |
|---|---|
| [`docs/ENDPOINTS.md`](docs/ENDPOINTS.md) | 全部已验证路由的方法、请求体与语义，附 Leader/Member 权限矩阵 |
| [`docs/PIPELINE.md`](docs/PIPELINE.md) | 筛选管线 SOP：阶段、质量闸门、编排纪律与报告诚信 |
| [`docs/PITFALLS.md`](docs/PITFALLS.md) | 必须显式处理的平台行为：分页、导入字段映射、导出前置条件 |
| [`pipeline/README.md`](pipeline/README.md) | 管线快速开始与阶段表 |
| [`prompts/README.md`](prompts/README.md) | 筛选 prompt 的结构说明 |

## 适用范围与免责声明

* **非官方。** 本项目与 abstrackr 官方无任何隶属或背书关系。
* **服务条款。** 自动化访问可能与站点条款冲突。请使用自己的账号并自行承担风险。
* **不含凭据、不含综述数据。** 仓库内只有代码与文档；凭据从本地被 git 忽略的文件读取。
* **建议使用专用账号**做自动化，并以 Member 身份邀请进项目。Member 可筛选，
  但只有 Leader 能解盲或导出。
* **请求频率。** 客户端是单线程的，请求之间保留了短暂延迟，请不要去掉。

## 许可证

MIT — 见 [`LICENSE`](LICENSE)。
