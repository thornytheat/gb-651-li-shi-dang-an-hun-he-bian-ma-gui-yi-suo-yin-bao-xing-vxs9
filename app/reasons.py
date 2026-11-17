REASONS = {
    "BAD_COLUMNS": "字段数量不符",
    "EMPTY_VALUE": "必要值为空",
    "DUPLICATE_CONFLICT": "同一业务键取值冲突",
    "TRUNCATED": "尾记录不完整",
    "INVALID_VALUE": "值不符合定义",
    "STALE_VERSION": "提交版本已过期",
    "TEMPLATE_MISMATCH": "文件列与模板不符",
    "TEMPLATE_MISSING": "模板版本不存在",
    "DECODE_FAILED": "文件无法按受支持编码解码",
    "HEADER_MISSING": "文件缺少表头",
    "BATCH_CONFLICT": "同一批号已接收不同内容",
}


def reason(code: str | None) -> str:
    if code is None:
        return "OK"
    return REASONS.get(code, "未登记原因")
