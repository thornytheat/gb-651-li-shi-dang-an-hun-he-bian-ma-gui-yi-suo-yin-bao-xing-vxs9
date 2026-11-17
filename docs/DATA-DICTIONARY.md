# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。

## 接收模板与批次

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `template.version` | string | 否 | 模板版本号，发布后不可修改 |
| `template.revision` | integer | 否 | 单调递增的发布修订号，用于并发发布判定 |
| `template.columns[]` | object | 否 | 列定义 `{name, role, required, literal_na}`，顺序即文件列顺序 |
| `template.null_tokens` | string[] | 否 | 该版本认定的空值字面量，必须含空串 |
| `template.allow_literal_na_archive_id` | boolean | 否 | 字面 `N/A` 是否为合法档号值 |
| `batch_no` | string | 否 | 批次号，接收幂等键；缺省按内容摘要生成 |
| `template_version` / `template_revision` | string / integer | 否 | 批次接收瞬间绑定的模板快照 |
| `source_line` | integer | 否 | 原文件物理起始行号（多行引号字段取首行） |
| `seq` | integer | 否 | 文件内记录序号，用于断点续收 |
| `state` | string | 否 | `valid` / `quarantine` / `empty` |
| `reason_code` | string | 是 | `BAD_COLUMNS`、`EMPTY_VALUE`、`TRUNCATED`、`TEMPLATE_MISMATCH` 等 |
| `raw` | string[] | 否 | 原样单元格，未做任何数值化 |
| `identity` | object | 是 | 有效记录的业务身份；旧模板为 `{archive_id}`，新模板为 `{fonds_no,catalog_no,archive_id}` |

### 列角色

| 角色 | 含义 |
|---|---|
| `archive_id_merged` | 旧模板的合并档号列 |
| `fonds_no` / `catalog_no` | 新模板拆出的全宗号 / 目录号 |
| `archive_id` | 新模板拆列后的档号；允许字面 `N/A` |
| `box_no` / `title` | 盒号 / 题名 |
| `other` | 不参与业务判定的附加列 |

批次绑定后模板快照不可变：接收中途发布新模板不会改变已开始批次所依据的列、空值约定与行号。
