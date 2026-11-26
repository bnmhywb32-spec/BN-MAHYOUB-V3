"""Fixed-price, whole-unit synthetic sale arithmetic shared by the sync fixture."""

MAX_SALE_QUANTITY = (2**63 - 1) // 100


def sale_journal(quantity):
    return (
        ("cash", quantity * 100),
        ("revenue", -quantity * 100),
        ("cogs", quantity * 60),
        ("inventory", -quantity * 60),
    )
