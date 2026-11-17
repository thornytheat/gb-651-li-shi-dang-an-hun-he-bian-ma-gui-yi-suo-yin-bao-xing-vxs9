# 历史档案混合编码归一与索引保形

面向一线复核人员的单服务数据工作台。样例数据随仓库提供，结论保留行号、原因码和原值。

## 业务模块

- M1 文件采信：`/api/intake/run`
- M2 编码判定：`/api/encoding/detect`
- M3 字段保形：`/api/fields/normalize`，接收模板与批次：`/api/fields/templates`、`/api/fields/receive`、`/api/fields/receive/upload`、`/api/fields/batches`
- M4 内容归一：`/api/content/normalize`
- M5 索引对账：`/api/reconcile`、`/api/reconcile/batch/{batch_no}`
- M6 批次导出：`/api/export/evidence.csv`（可加 `?batch_no=`）

## 核心规则

候选解码必须完整且无替换字符；档号、盒号与超大流水号始终按字符串逐字保留。

## 接收模板与批次

字段页 `/fields` 同时提供模板发布与实际文件接收：

1. 模板按版本发布，`revision` 单调递增，发布后定义不可修改；并发发布时旧提交返回 409 `STALE_VERSION`。
2. 每个批次在接收开始时绑定当时的活动模板（或请求中显式指定的版本），并保存模板快照。
3. 旧模板沿用合并列 `档号` 与空值约定（`N/A` 等视为空）；新模板拆为 `全宗号`、`目录号`、`档号`，只把空串视为空，字面 `N/A` 是合法档号值。
4. 回执保留原列名与列序、原文件物理行号、原样单元格与前导零。
5. 同批号 + 同内容重试返回原回执且不新增记录；同批号 + 不同内容返回 409 `DUPLICATE_CONFLICT`。
6. 接收按 `seq` 幂等落盘并记录 `next_seq`，中断后可在 `/api/fields/batches/{batch_no}/resume` 续收。
7. 每批满足 输入 = 有效 + 隔离 + 空，有效 + 隔离之和可单独核对。

## 启动

```bash
docker compose up --build
```

浏览器访问 `http://localhost:18210`。运行测试：

```bash
docker compose exec -T app pytest -q
```

## 样例流程

1. 在字段页以默认模板接收 `sample_data/archive_dirty.csv`（合并列旧批次）。
2. 发布新版模板 `2026.12`（`preset=split`），再接收 `sample_data/archive_split.csv`。
3. 重试旧批次，回执应与升级前逐字一致；新批次按新列、新空值约定出回执。
4. 在索引对账与批次导出中核对有效、隔离与合计。

## 已知不覆盖

不处理加密文件与需要外部密钥的对象；不连接真实生产存储。
