from buggy_discount import apply_discount


def test_gold():
    price = 120.0
    loyalty_level = "gold"
    actual = apply_discount(price, loyalty_level)
    assert actual == 102.0
