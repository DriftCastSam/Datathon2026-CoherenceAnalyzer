from portee import levels


def test_order_and_clamp():
    assert levels.lower("low_moderate", "high") == "low_moderate"
    assert levels.clamp_move("low_moderate", "high") == "moderate"
    assert levels.clamp_move("moderate", "low") == "low_moderate"
    assert levels.step("low", -1) == "low"
