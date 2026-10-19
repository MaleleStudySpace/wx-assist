"""Optional ONNX acceleration; never require a GPU to load the application."""

PROVIDERS = {
    "cuda": "CUDAExecutionProvider",
    "directml": "DmlExecutionProvider",
    "cpu": "CPUExecutionProvider",
}
LABELS = {"cuda": "NVIDIA CUDA", "directml": "DirectML 显卡加速", "cpu": "CPU"}


def capabilities():
    try:
        import onnxruntime
        available = onnxruntime.get_available_providers()
    except ImportError:
        available = []
    return {name: provider in available for name, provider in PROVIDERS.items()}


def select_device(requested, available):
    if requested not in ("auto", *PROVIDERS):
        raise ValueError("未知的 RAG 加速模式")
    if requested == "auto":
        return next((name for name in ("cuda", "directml", "cpu") if available.get(name)), "cpu")
    return requested if available.get(requested) else "cpu"


def create_model(model_name, cache_dir, threads, device):
    if device == "directml":
        from fastembed.text.onnx_embedding import OnnxTextEmbedding
        import onnxruntime as ort

        class DirectMLEmbedding(OnnxTextEmbedding):
            # DirectML requires sequential execution and no memory pattern.
            EXPOSED_SESSION_OPTIONS = (*OnnxTextEmbedding.EXPOSED_SESSION_OPTIONS, "enable_mem_pattern")

            @classmethod
            def add_extra_session_options(cls, options, extra):
                options.enable_mem_pattern = False
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                super().add_extra_session_options(options, {
                    key: value for key, value in extra.items() if key != "enable_mem_pattern"
                })

        return DirectMLEmbedding(
            model_name=model_name, cache_dir=cache_dir, threads=threads,
            providers=[PROVIDERS[device], PROVIDERS["cpu"]], enable_mem_pattern=False,
        )
    from fastembed import TextEmbedding
    return TextEmbedding(
        model_name=model_name, cache_dir=cache_dir, threads=threads,
        providers=[PROVIDERS[device]],
    )


def active_device(model):
    # TextEmbedding wraps an ONNX model; DirectMLEmbedding is already that model.
    current = model
    for _ in range(4):
        if hasattr(current, "get_providers"):
            active = current.get_providers()
            return next((name for name in ("cuda", "directml", "cpu") if PROVIDERS[name] in active), "cpu")
        current = getattr(current, "model", None)
    return "cpu"
