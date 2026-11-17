# 深度登记 历史档案混合编码归一与索引保形

- 规模：中型
- 核心规则：候选解码必须完整且无替换字符；档号、盒号与超大流水号始终按字符串逐字保留。
- 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

## 功能模块

| 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
|---|---|---|---|---|
| M1 文件采信 | 运行样例并复核证据 | `/api/intake/run` / `/intake` | `app/modules/m1_intake/core.py` `app/modules/m1_intake/service.py` | `tests/test_m1_intake.py` |
| M2 编码判定 | 运行样例并复核证据 | `/api/encoding/detect` / `/encoding` | `app/modules/m2_encoding/core.py` `app/modules/m2_encoding/service.py` | `tests/test_m2_encoding.py` |
| M3 字段保形 | 发布接收模板、上传实际文件、复核回执 | `/api/fields/normalize` `/api/fields/templates` `/api/fields/receive*` `/fields` | `app/modules/m3_fields/core.py` `app/modules/m3_fields/service.py` `app/modules/m3_fields/templates.py` `app/store.py` | `tests/test_m3_fields.py` `tests/test_m3_templates.py` `tests/test_m3_receiving.py` `tests/test_m3_e2e_upgrade.py` |
| M4 内容归一 | 运行样例并复核证据 | `/api/content/normalize` / `/content` | `app/modules/m4_content/core.py` `app/modules/m4_content/service.py` | `tests/test_m4_content.py` |
| M5 索引对账 | 运行样例并按批次对账 | `/api/reconcile` `/api/reconcile/batch/{batch_no}` / `/reconcile` | `app/modules/m5_reconcile/core.py` `app/modules/m5_reconcile/service.py` | `tests/test_m5_reconcile.py` `tests/test_m3_receiving.py` |
| M6 批次导出 | 导出证据清单（可按批次） | `/api/export/evidence.csv` `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |

## 陷阱登记

| TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
|---|---|---|---|---|---|
| TRAP-01 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_1` |
| TRAP-02 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_1` |
| TRAP-03 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_1` |
| TRAP-04 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_reconcile/core.py` | `tests/test_m5_reconcile.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-08 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_intake/core.py` | `tests/test_m1_intake.py::test_trap_2` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_encoding/core.py` | `tests/test_m2_encoding.py::test_trap_2` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_fields/core.py` | `tests/test_m3_fields.py::test_trap_2` |
| TRAP-18 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_content/core.py` | `tests/test_m4_content.py::test_trap_2` |
| TRAP-21 | 模板升级后旧批次重试 | 用当前模板重算，合并列被拆、`N/A` 变空、行号漂移 | 批次接收瞬间快照模板，重试直接返回原回执 | `app/store.py` `app/modules/m3_fields/service.py` | `tests/test_m3_e2e_upgrade.py::test_upgrade_keeps_old_and_accepts_new` |
| TRAP-22 | 并发发布模板 | 后写覆盖先写，批次绑定到半套规则 | `BEGIN IMMEDIATE` 下比较修订号，过期发布返回 409 `STALE_VERSION` | `app/store.py::publish_template` | `tests/test_m3_receiving.py::test_concurrent_publication_only_one_wins` |
| TRAP-23 | 接收中断后重启 | 重跑从头插入，记录翻倍或批次永远停在半途 | 按 `seq` 幂等落盘并持久化 `next_seq`，可从断点续收 | `app/store.py::append_progress` `app/modules/m3_fields/service.py::_advance` | `tests/test_m3_receiving.py::test_resume_after_restart_uses_persisted_db` |
| TRAP-24 | 同批号换内容 | 静默覆盖旧批次证据 | 同批号 + 同内容返回原回执；不同内容 409 `DUPLICATE_CONFLICT` | `app/modules/m3_fields/service.py::receive_bytes` | `tests/test_m3_receiving.py::test_same_batch_no_different_content_rejected` |
| TRAP-25 | 隔离坏行后核对合计 | 只统计有效行，隔离行被吞 | 输入 = 有效 + 隔离 + 空，且有效 + 隔离可单独核对 | `app/modules/m5_reconcile/core.py::reconcile_receipt` | `tests/test_m3_receiving.py::test_receipt_quarantine_and_valid_sum_to_input` |

## 对账规则

- 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
- 核心数值或事件数在转换、判定和导出之间保持守恒。
- 同一输入摘要重跑复用原运行编号，不增加结果行。
- 同一批号 + 同一内容重试返回原回执，不增加记录；同一批号 + 不同内容拒绝。
- 每批输入 = 有效 + 隔离 + 空；有效 + 隔离之和单独可核对。

## 模板升级流程

- 模板按版本发布，修订号单调递增；发布后定义不可修改。
- 批次在接收开始时绑定当时的活动模板（或显式指定版本），并保存模板快照。
- 升级后旧批次按快照返回原回执；新批次按新模板接收，列名、列序、空值约定各自独立。
- 删除或覆盖模板被禁止，历史回执始终可重放。

## 已知不覆盖

- 生产系统连接、身份认证和外部密钥不在演示范围内。
