from temnia_pipeline.speech.coverage import CoverageThresholds, assess_coverage


def test_clear_is_explicitly_detector_agreement() -> None:
    result = assess_coverage([(100, 1_000)], [(90, 1_100)], 2_000)
    assert result.status == "clear"
    assert "agreement only" in result.warnings[0]


def test_internal_and_tail_omissions_route_to_review() -> None:
    thresholds = CoverageThresholds(
        largest_uncovered_ms=1_000,
        total_uncovered_ms=2_000,
        uncovered_ratio=0.2,
        tail_uncovered_ms=500,
    )
    internal = assess_coverage(
        [(0, 2_000), (4_000, 6_000)], [(0, 2_000)], 6_000, thresholds=thresholds
    )
    assert internal.status == "needs_review"
    assert internal.uncovered_tail_ms == 2_000
    assert internal.warnings == ["detector and recognition disagree materially; listen and review"]


def test_detector_failure_and_double_empty_are_unknown() -> None:
    failed = assess_coverage(None, [(0, 100)], 1_000, detector_error="onnx failed")
    empty = assess_coverage([], [], 1_000)
    assert failed.status == empty.status == "unknown"
    assert failed.warnings == ["onnx failed"]
    assert "not proof of silence" in empty.warnings[0]


def test_recognition_without_detector_support_is_unknown() -> None:
    result = assess_coverage([], [(0, 1_000)], 1_000)
    assert result.status == "unknown"
    assert result.recognition_outside_detector_ms == 1_000
    assert "found none" in result.warnings[0]
