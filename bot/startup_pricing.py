"""Display-only proposed launch prices; never used by payment or order services."""

MONTHLY_RUB = 190
QUARTERLY_RUB = 490
ANNUAL_RUB = 1690


def saving_against_monthly(months: int, total_rub: int) -> int:
    return MONTHLY_RUB * months - total_rub
