# 数据字典

| 字段 | 类型 | 可空 | 定义 |
|---|---|---:|---|
| `run_id` | string | 否 | 输入摘要派生的稳定运行编号 |
| `source_line` | integer | 否 | 原文件物理行号 |
| `reason_code` | string | 是 | 隔离或冲突的稳定原因码 |
| `raw_value` | string | 是 | 未归一的原始证据 |
| `normalized_value` | string | 是 | 按项目规则得到的值 |
| `rule_version` | string | 否 | 计算规则版本 |
| `zone` | string | 是 | 解释无时区日志采用的 IANA 时区 |
| `effective_from` | string | 否 | 更正生效起点，服务本地时间，左闭 |
| `base_version` | integer | 否 | 提交更正时基于的决定版本，用于并发控制 |
| `fold` | integer | 是 | 夏令时重叠时段选择：0 首次 / 1 再次 |
| `correction_version` | integer | 否 | 解释该条日志所用的更正决定版本，0 表示基础时区 |

字符串编号逐字保留。金额与计量值用 `Decimal`。时间值带偏移并以 UTC 参与比较。区间统一采用左闭右开。

## M4 隔离原因码

| 原因码 | 定义 |
|---|---|
| `DST_GAP` | 本地时间落在夏令时跳时空档，fold 无法挽救 |
| `FOLD_REQUIRED` | 本地时间落在夏令时重叠时段且未提供 fold 选择 |
| `ZONE_REQUIRED` | 无时区日志且服务未登记任何时区 |
| `STALE_VERSION` | 更正提交基于过期决定版本，被拒绝 |
