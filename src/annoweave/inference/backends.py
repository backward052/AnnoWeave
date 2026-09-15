from __future__ import annotations

from typing import Any

CPU_PROVIDER = "CPUExecutionProvider"


def onnxruntime_available() -> bool:
    """onnxruntime 是否可用（缺失时应用其余部分仍应能运行）。"""
    try:
        import onnxruntime  # noqa: F401, PLC0415
    except Exception:  # noqa: BLE001
        return False
    return True


def requested_providers(device: str) -> list[str]:
    """按配置的设备偏好给出 provider 优先级列表。"""
    text = (device or "cpu").strip().lower()
    if text in ("cuda", "gpu"):
        return ["CUDAExecutionProvider", CPU_PROVIDER]
    if text in ("dml", "directml"):
        return ["DmlExecutionProvider", CPU_PROVIDER]
    if text == "coreml":
        return ["CoreMLExecutionProvider", CPU_PROVIDER]
    return [CPU_PROVIDER]


def available_providers() -> list[str]:
    """实际可用的执行提供者；onnxruntime 缺失时返回空列表。"""
    try:
        import onnxruntime as ort  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:  # noqa: BLE001
        return []


class ONNXEngine:
    """ONNX Runtime 会话封装：解析输入张量名，按设备选择执行提供者。

    惰性导入 onnxruntime，保证在未安装时应用其余部分仍可运行。
    `active_provider` 记录**实际生效**的 provider：请求 CUDA 但运行时只给了
    CPU 时会被如实暴露，不能把「请求 GPU」当成「已在 GPU 上执行」。
    """

    def __init__(self, model_path: str, device: str = "cpu") -> None:
        import onnxruntime as ort  # noqa: PLC0415

        self.requested_device = device
        self.providers = requested_providers(device)
        self.session = ort.InferenceSession(model_path, providers=self.providers)
        self.input_name = self.session.get_inputs()[0].name
        self.output_names = [output.name for output in self.session.get_outputs()]
        self.active_provider = self._detect_active_provider()

    def _detect_active_provider(self) -> str:
        """读取会话实际使用的 provider，取不到时保守地认为在 CPU 上。"""
        try:
            active = self.session.get_providers()
        except Exception:  # noqa: BLE001
            active = []
        for name in active:
            if name != CPU_PROVIDER:
                return name
        return active[0] if active else CPU_PROVIDER

    @property
    def fell_back_to_cpu(self) -> bool:
        return (
            self.providers
            and self.providers[0] != CPU_PROVIDER
            and self.active_provider == CPU_PROVIDER
        )

    def device_report(self) -> str:
        if self.fell_back_to_cpu:
            return (
                f"请求 {self.providers[0]}，实际 {self.active_provider}"
                "（未生效，推理在 CPU 上执行）"
            )
        return f"实际 {self.active_provider}"

    def run(self, tensor: Any) -> Any:
        results = self.session.run(None, {self.input_name: tensor})
        return results[0] if len(results) == 1 else results


def create_backend(model_config) -> ONNXEngine:
    """按配置创建推理后端。当前仅实现 ONNX Runtime；其他后端留扩展位。"""
    return ONNXEngine(model_config.model_path, model_config.device)
