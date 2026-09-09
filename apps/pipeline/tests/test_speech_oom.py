from temnia_pipeline.speech.oom import is_resource_oom, next_batch_size


def test_only_explicit_oom_advances_bounded_batches() -> None:
    oom = RuntimeError("CUDA out of memory while allocating tensor")
    assert is_resource_oom(oom)
    assert next_batch_size(16, oom) == 8
    assert next_batch_size(8, oom) == 4
    assert next_batch_size(4, oom) is None
    assert next_batch_size(16, TimeoutError("transport unknown")) is None
    assert next_batch_size(16, RuntimeError("failed to allocate disk buffer")) is None
    assert next_batch_size(16, RuntimeError("CTranslate2 failed to allocate GPU buffer")) == 8
    assert next_batch_size(32, oom) is None
