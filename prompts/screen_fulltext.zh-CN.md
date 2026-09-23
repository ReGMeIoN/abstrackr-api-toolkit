# 全文层逐批判定 prompt（模板 · 中文）

> **上游**：题摘层的 Hold 池（`pipeline/s5_hold_pool.py` 产出的 `hold_fulltext.json`）。
> **本阶段只做一件事**：**纳入 / 排除确认**。**不做数据提取**（效应量只"字面抄录"，不进提取表）。
> **配套**：人工取全文 → AI 判合格性 → 人工复核（全部 Include + 随机 ≥20% Exclude）。

---

## 占位符清单

| 占位符 | 含义 |
|---|---|
| `{{PROJECT_NAME}}` | 综述题目 |
| `{{RUBRIC_PATH}}` | 判据文件绝对路径（PECO + 纳入标准 + 排除编码表） |
| `{{HOLD_JSON_PATH}}` | Hold 池文件绝对路径 |
| `{{FT_BATCH_FILE}}` | 本批全文批次文件（含全文路径） |
| `{{FT_DECISION_DIR}}` | 全文判定输出目录 |
| `{{BATCH}}` `{{N}}` | 批号 / 本批条数（建议每批 3–8 条；单条全文 >60,000 字符时一条一批） |
| `{{EXCLUDE_CODES}}` | 排除编码表（编码 → 含义 → 去向）；**编码集由 protocol 定义，不写死在模板里** |
| `{{OUTCOME_DEFINITION}}` | 结局口径 |

---

## 定位：为什么全文层要单独一套

| | 题摘层 | 全文层（本文件） |
|---|---|---|
| 输入 | 题名 + 摘要 | **原始题摘判定 + 全文文本** |
| 输出 | Exclude / **Hold** / Review | **Include / Exclude + 排除编码** |
| 判定依据 | 只有题摘，信息不足一律 Hold | 有全文，**必须给出确定结论** |
| 是否产出最终纳入 | ❌ 不产出 | ✅ 产出（仍需人工复核） |
| 数据提取 | ❌ | ❌（另开一步） |

**关键纪律：全文层 AI 也不能单独定最终纳入。** 人工全部复核 Include、随机复核 Exclude。

---

## 模板正文（复制以下内容）

```text
你是 {{PROJECT_NAME}} 系统综述的**全文筛选执行方**。只依据给定的全文文本与判据工作：
不联网、不补知识、不推断未写明的内容。

【输入】
1. 判据（必读）：{{RUBRIC_PATH}}
2. Hold 清单（上游产物，用于核对 i 与上游判定）：{{HOLD_JSON_PATH}}
3. 本批记录与全文：{{FT_BATCH_FILE}}
   （含题摘层判定 c / hold_reason / need，以及 fulltext_path）

【任务】对每条记录，读该记录的全文文本，判定 Include 或 Exclude，并给出排除编码。

【必须遵守的三条】
 1. 同一条记录 i：全文判定的 i 必须与题摘层完全一致，不得重新编号、不得合并、不得遗漏。
 2. 同一套判据：不得自创编码。
 3. 不联网、不补知识：只依据给定的全文文本；全文里没写的一律不得推断。
    取不到全文 → 无法获取编码；取到但不完整（如缺附表）→ 在 reason 里写明缺哪一部分。

【判定规则（按顺序，命中即停）】
{{EXCLUDE_CODES}}
 （以上都不触发，且满足全部纳入标准 → Include。Include 必须能提取到可用的
  效应量，且能从文中分辨暴露组与对照组。）

【硬性纪律】
 · 只用给定的全文文本。不得用摘要里的信息补全文缺的内容；不得凭记忆补数据。
 · **效应量必须字面抄录**：只抄全文里明写的数字（含其 95%CI 与单位），
   一律不得推算、换算、编造。找不到就留空串。
 · 结局口径：{{OUTCOME_DEFINITION}}
 · 拿不准 Include 还是 Exclude 时：**选 Exclude 并在 reason 里写明不确定点**，
   由人工复核判定 —— 全文层不允许用"Maybe"逃逸。
 · 不得做数据提取：除了 decision / exclude_code / reason / evidence / eff，不要输出别的字段。
 · 改判不是删除：若某编码对应的是"转移去向"（如转敏感性分析 / 转三角验证），
   reason 里必须写明去向。

【输出：严格 JSON，写入】
{{FT_DECISION_DIR}}\{{BATCH}}.decisions.json

{"batch":"{{BATCH}}","n":{{N}},"decisions":[
  {"i":123,"decision":"Include","exclude_code":"","reason":"一句话理由",
   "evidence":"原文片段 ≤200 字符","eff":"SIR 1.42 (95% CI 1.05-1.92)","conf":"high"}
]}
 · decision：只允许 "Include" 或 "Exclude"
 · exclude_code：Exclude 时必须是 {{EXCLUDE_CODES}} 之一；Include 时为空串
 · reason ≤200 字符；evidence ≤200 字符（Include 必须有；Exclude 可空）
 · eff：字面效应量，无则空串
 · conf：high / medium / low

【完成前自检】
 · len(decisions) == n；i 集合与输入完全一致
 · decision ∈ {Include, Exclude}；Exclude 必有 exclude_code；Include 的 exclude_code 必须为空
 · Include 的 evidence 非空；所有 eff 都不是推算出来的

只回一行：{{BATCH}} done: n=xx, Include=xx Exclude=xx, validated
```

---

## 闭合检查（全文层特有的质量闸门）

题摘层的 Hold 池是一个**有界队列**，所以全文层可以做题摘层做不到的检查：

| 闸门 | 判据 |
|---|---|
| **Hold 闭合** | 全文层回来的 Include + Exclude 必须**覆盖全部 Hold 记录**，无遗漏、无新增 i |
| Include 全部人工复核 | 逐条核对：效应量是否抄对、结局口径是否是目标结局 |
| Exclude 随机复核 | 随机抽 ≥20% 或 ≥50 条（取大者）核对排除编码 |
| 特殊编码全查 | "转移去向"类编码（转敏感性分析 / 转三角验证 / 待作者回复）全部人工过一遍 |
| 与题摘层不一致的改判 | 全部人工过一遍，并保留"题摘→全文"的改判链路标签 |

回写平台时**保留**题摘层的标签，不删除，只追加全文层标签并把 Maybe 改成最终值——
这样任何一条记录的判定历史都可追溯。
