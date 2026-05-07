def lookup_rate(loyalty_level):
    rates = {
        "bronze": 0.05,
        "silver": 0.10,
        "gold": 0.15,
    }
    return rates.get(loyalty_level, 0.0)


def apply_discount(price, loyalty_level):
    rate = lookup_rate(loyalty_level)  # STEP_INTO_LOOKUP
    discounted = price - rate  # BUG: should subtract price * rate.
    return round(discounted, 2)


def main():
    price = 120.0
    loyalty_level = "gold"
    total = apply_discount(price, loyalty_level)  # BREAK_MAIN_CALL
    print(f"{loyalty_level=} {price=} {total=}")


if __name__ == "__main__":
    main()
