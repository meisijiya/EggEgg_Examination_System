# 题目库导入报告

> **操作时间**: 2026-07-04 14:11:48 UTC
> **阶段**: P4（修正 ch9 #12 + 构建 SQLite 数据库）

## 1. ch9 #12 options 修正

| 字段 | 值 |
|---|---|
| ID | `42c801e33547db0e` |
| 章节 | ch9 # 12 |
| 答案（不变） | `C` |

**修正前 options**：

```json
[
  "A、成本 C、期间费用",
  "B、销售收入",
  "C、利润"
]
```

**修正后 options**：

```json
[
  "A、成本",
  "B、销售收入",
  "C、利润",
  "D、期间费用"
]
```

**依据**：PDF 文本层布局（page 5，word 坐标）显示：

- `y=330.21`: `A.成本` (x=90) + `C、期间费用` (x=133.20) → 同 y 不同 x = 视觉错位
- `y=361.41`: `B.销售收入`
- `y=392.61`: `C.利润`

→ 原始 PDF 把第 4 个选项错位渲染到第 1 行尾部，恢复为 D。
→ Oracle 审查报告 §4.3 已确认此处置（方案 b：修正后入库）。

## 2. Pydantic 严格校验

- questions.jsonl: **565/565** 通过（`extra='forbid'`）
- 错误数: **0** ✅

| 文件 | 行数 | 状态 |
|---|---|---|
| `difficulty/ch1.jsonl` | 44 | ✅ |
| `difficulty/ch2.jsonl` | 39 | ✅ |
| `difficulty/ch3.jsonl` | 39 | ✅ |
| `difficulty/ch4.jsonl` | 57 | ✅ |
| `difficulty/ch5.jsonl` | 100 | ✅ |
| `difficulty/ch6.jsonl` | 97 | ✅ |
| `difficulty/ch7.jsonl` | 49 | ✅ |
| `difficulty/ch8.jsonl` | 46 | ✅ |
| `difficulty/ch9.jsonl` | 94 | ✅ |

## 3. SQLite 数据库 (`data/final/finance.db`)

- 文件大小: **262,144 bytes** (256.0 KB)

| 表 | COUNT(*) |
|---|---|
| `subjects` | 1 |
| `chapters` | 9 |
| `questions` | 565 |

**Schema 摘要**（按用户 P4 指令）：

- `difficulty` 为 INTEGER 1/2/3（CHECK 约束）
- `options_json`: 单选/多选/判断存为 JSON 数组；其他 NULL
- `key_points_json`: 主观题存为 JSON 数组；客观题 NULL
- 未创建 `exam_attempts` / `attempt_answers`（运行时用）

## 4. Rejected 题

- 总数: **0**
- ✅ 无 rejected 题

## 5. 输出文件清单

| 路径 | 说明 |
|---|---|
| `data/final/finance.db` | SQLite 数据库 |
| `data/qa/import_report.md` | 本报告 |
| `data/qa/rejected.jsonl` | rejected 题列表（id/chapter/stem/reason） |
| `data/parsed/questions.jsonl` | 已修正 ch9 #12 |
| `data/parsed/questions.bak.jsonl` | 修正前备份 |
| `data/parsed/difficulty/ch9.jsonl` | 同步校验（difficulty 文件无 options 字段，无需内容变更） |
