# 用于代码解析/切块 fixture 的极简示例，保持逻辑稳定以便断言定位。
def calculate_nebula_window(samples: int) -> int:
    """按既定倍率计算 fixture 使用的 Nebula 窗口大小。"""
    return samples * 19
