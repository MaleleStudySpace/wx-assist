from unittest.mock import Mock

import pytest

pytest.importorskip("numpy")
from src.assistant import rag_status
from src.assistant.config import _config_to_dict, _dict_to_config
from src.assistant.rag import acceleration
from src.assistant.rag.embedder import FastEmbedder


def test_device_selection_and_unavailable_gpu():
    assert acceleration.select_device("auto", {"cuda": True, "cpu": True}) == "cuda"
    assert acceleration.select_device("auto", {"directml": True, "cpu": True}) == "directml"
    assert acceleration.select_device("cuda", {"cpu": True}) == "cpu"
    assert acceleration.select_device("cpu", {"cuda": True, "cpu": True}) == "cpu"
    with pytest.raises(ValueError):
        acceleration.select_device("other", {})


def test_gpu_failure_retries_cpu_and_reports_actual_device(monkeypatch):
    monkeypatch.setattr(acceleration, "capabilities", lambda: {"cuda": True, "cpu": True})
    cpu = Mock()
    cpu.embed.return_value = [[1.0, 0.0]]
    factory = Mock(side_effect=[RuntimeError("missing CUDA DLL"), cpu])
    monkeypatch.setattr(acceleration, "create_model", factory)
    monkeypatch.setattr(acceleration, "active_device", lambda model: "cpu")
    embedder = FastEmbedder(device="auto")
    embedder.warmup()
    assert embedder.dim == 2
    assert factory.call_args_list[0].args[-1] == "cuda"
    assert factory.call_args_list[1].args[-1] == "cpu"
    assert rag_status.snapshot()["hardware"]["active"] == "cpu"
    assert rag_status.snapshot()["hardware"]["fallback_reason"]


def test_gpu_out_of_memory_retries_entire_batch_on_cpu(monkeypatch):
    embedder = FastEmbedder(device="directml")
    embedder._device = "directml"
    embedder._model = Mock()
    embedder._model.embed.side_effect = RuntimeError("out of memory")
    cpu = Mock()
    cpu.embed.side_effect = [[[1.0, 0.0]], [[2.0, 3.0]]]
    monkeypatch.setattr(acceleration, "create_model", lambda *args: cpu)
    result = embedder.encode(["text"])
    assert result.tolist() == [[2.0, 3.0]]
    assert rag_status.snapshot()["hardware"]["active"] == "cpu"


def test_config_roundtrip_and_invalid_values():
    cfg = _dict_to_config({"rag_device": "directml", "rag_batch_size": 8})
    data = _config_to_dict(cfg)
    assert data["rag_device"] == "directml"
    assert data["rag_batch_size"] == 8
    cfg = _dict_to_config({"rag_device": "invalid", "rag_batch_size": 4096})
    assert cfg.rag_device == "auto"
    assert cfg.rag_batch_size == 16


def test_progress_keeps_completed_work_on_error_and_snapshot_is_detached():
    rag_status.reset("ready")
    rag_status.job("test", total=100, completed=0)
    rag_status.job("test", completed=16)
    rag_status.job("test", state="error", error="failed")
    snapshot = rag_status.snapshot()
    assert snapshot["jobs"]["test"]["completed"] == 16
    assert snapshot["jobs"]["test"]["total"] == 100
    snapshot["jobs"]["test"]["total"] = 0
    assert rag_status.snapshot()["jobs"]["test"]["total"] == 100
    rag_status.reset()
