REASONS = {
    "BAD_COLUMNS": "字段数量不符", "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突", "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义", "STALE_VERSION": "提交版本已过期",
    "DST_GAP": "本地时间落在夏令时跳时空档",
    "FOLD_REQUIRED": "夏令时重叠时段缺少 fold 选择",
    "ZONE_REQUIRED": "无时区日志且服务未登记时区",
    "INVALID_ZONE": "时区名称无法识别",
}

def reason(code: str) -> str:
    return REASONS.get(code, "未登记原因")
