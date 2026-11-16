# 深度登记 多时区应用日志小时桶重算

        - 规模：中型
        - 核心规则：事件先归一到 UTC，DST 重叠时保留 fold；小时桶右端开，水位只能前进且迟到事件仍进入修订。
        - 使用者与工作流：导入随仓样例，逐模块复核，最后导出单一证据清单。

        ## 功能模块

        | 模块 | 使用者动作 | 入口 | 核心文件 | 测试 |
        |---|---|---|---|---|
        | M1 日志采信 | 运行样例并复核证据 | `/api/logs/import` / `/logs` | `app/modules/m1_logs/core.py` `app/modules/m1_logs/service.py` | `tests/test_m1_logs.py` |
| M2 时间解析 | 运行样例并复核证据 | `/api/time/normalize` / `/time` | `app/modules/m2_time/core.py` `app/modules/m2_time/service.py` | `tests/test_m2_time.py` |
| M3 事件去重 | 运行样例并复核证据 | `/api/events/dedupe` / `/events` | `app/modules/m3_events/core.py` `app/modules/m3_events/service.py` | `tests/test_m3_events.py` |
| M4 小时分桶 | 运行样例并复核证据 | `/api/buckets/rebuild` / `/buckets` | `app/modules/m4_buckets/core.py` `app/modules/m4_buckets/service.py` | `tests/test_m4_buckets.py` |
| M5 迟到修订 | 运行样例并复核证据 | `/api/watermark/advance` / `/watermark` | `app/modules/m5_watermark/core.py` `app/modules/m5_watermark/service.py` | `tests/test_m5_watermark.py` |
| M6 批次对账 | 运行样例并复核证据 | `/api/export/buckets.csv` / `/runs` | `app/modules/m6_runs/core.py` `app/modules/m6_runs/service.py` | `tests/test_m6_runs.py` |

        ## 陷阱登记

        | TRAP | 现象 | 朴素实现 | 本项目处理 | 处理位置 | 测试 |
        |---|---|---|---|---|---|
        | TRAP-03 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_logs/core.py` | `tests/test_m1_logs.py::test_trap_1` |
| TRAP-05 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_time/core.py` | `tests/test_m2_time.py::test_trap_1` |
| TRAP-07 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_events/core.py` | `tests/test_m3_events.py::test_trap_1` |
| TRAP-09 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_buckets/core.py` | `tests/test_m4_buckets.py::test_trap_1` |
| TRAP-11 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m5_watermark/core.py` | `tests/test_m5_watermark.py::test_trap_1` |
| TRAP-12 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m6_runs/core.py` | `tests/test_m6_runs.py::test_trap_1` |
| TRAP-13 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m1_logs/core.py` | `tests/test_m1_logs.py::test_trap_2` |
| TRAP-14 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m2_time/core.py` | `tests/test_m2_time.py::test_trap_2` |
| TRAP-16 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m3_events/core.py` | `tests/test_m3_events.py::test_trap_2` |
| TRAP-23 | 真实边界输入 | 朴素处理会错分 | 保留证据并稳定裁决 | `app/modules/m4_buckets/core.py` | `tests/test_m4_buckets.py::test_trap_2` |

        ## 时区更正设计（M4 扩展）

        - 决定模型：`corrections` 表按 (service, version) 留存全部历次决定与理由，最新为 ACTIVE，历史为 SUPERSEDED；解析时取 `effective_from <= 本地时间` 中版本最高者，历史版本仍管辖其生效范围。
        - 复现保证：批次结果连同原日志、逐条证据（时区、fold、决定版本）封存于 `bucket_batches`，digest 主键幂等，重试复用原 `run_id`。
        - 并发与故障：`add_correction` 在 `BEGIN IMMEDIATE` 事务内校验 `base_version`，过期拒绝 `STALE_VERSION`；重算按块写 `bucket_checkpoints` 断点，中断凭同一令牌续跑；`commit_batch` 同事务完成批次落库、当前指针切换与断点清理，失败整体回滚沿用旧版。
        - 测试：`tests/test_m4_correction.py` 覆盖跳时/重叠隔离、生效范围、过期拒绝、幂等重试、中断续跑、切换失败与历史复现。

        ## 对账规则

        - 输入记录数 = 有效记录数 + 隔离记录数 + 空记录数。
        - 核心数值或事件数在转换、判定和导出之间保持守恒。
        - 同一输入摘要重跑复用原运行编号，不增加结果行。

        ## 已知不覆盖

        - 生产系统连接、身份认证和外部密钥不在演示范围内。
