"""设备工具: 按 CUDA 可用性决定模型精度。"""

def use_fp16() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False
