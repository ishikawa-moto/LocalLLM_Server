def total_with_tax(amount, rate):
    if amount < 0:
        raise ValueError("amount must not be negative")
    return amount + amount * rate
