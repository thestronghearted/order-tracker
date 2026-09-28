from service.policy import InFlightTracker


def test_second_start_for_same_key_is_refused_until_finished():
    tracker = InFlightTracker()
    assert tracker.try_start("HighErrorRate") is True
    assert tracker.try_start("HighErrorRate") is False  # already running
    assert tracker.try_start("OtherAlert") is True  # different key, unaffected

    tracker.finish("HighErrorRate")
    assert tracker.try_start("HighErrorRate") is True  # free again


def test_is_active_reflects_state():
    tracker = InFlightTracker()
    assert tracker.is_active("x") is False
    tracker.try_start("x")
    assert tracker.is_active("x") is True
    tracker.finish("x")
    assert tracker.is_active("x") is False
