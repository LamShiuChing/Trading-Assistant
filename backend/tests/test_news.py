from app.news import OFFERING_HINT, Tags, clean


def test_clean_strips_ibkr_metadata():
    assert clean("{A:800015:L:en}!Morgan Stanley upgraded MANH") == "Morgan Stanley upgraded MANH"
    assert clean("Plain headline") == "Plain headline"


def test_offering_keyword_hint():
    assert OFFERING_HINT.search("XYZ Announces Pricing of $200M Public Offering")
    assert OFFERING_HINT.search("ABC files for at-the-market program")
    assert not OFFERING_HINT.search("CEO Surrenders 61,554 Of Figs Inc")  # insider sale is not an offering


def test_tag_schema_rejects_unknown_catalyst():
    Tags.model_validate({"items": [{"idx": 0, "catalyst": "analyst", "direction": "bullish",
                                    "surprise": "surprise", "is_offering": False}]})
    try:
        Tags.model_validate({"items": [{"idx": 0, "catalyst": "rumor", "direction": "bullish",
                                        "surprise": "surprise", "is_offering": False}]})
        raise AssertionError("unknown catalyst accepted")
    except ValueError:
        pass
