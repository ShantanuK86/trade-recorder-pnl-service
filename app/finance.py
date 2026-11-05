"""Explicit arithmetic and storage limits shared by both database backends."""

from contextlib import contextmanager
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

PRECISION = 160
STORAGE_PRECISION = 120
STORAGE_SCALE = 36
COST_QUANTUM = Decimal("1e-36")
MAX_INPUT = Decimal("1e24")
MAX_QUANTITY = Decimal("1e30")
MAX_BALANCE = Decimal("1e70")


@contextmanager
def money_context():
    with localcontext() as context:
        context.prec = PRECISION
        context.rounding = ROUND_HALF_EVEN
        yield context


def decimal_text(value: Decimal) -> str:
    if not value:
        return "0"
    result = format(value, "f")
    return result.rstrip("0").rstrip(".") if "." in result else result


def positive_amount(value: object) -> Decimal:
    # JSON fractional numbers have already been rounded to float by the decoder.
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("use a decimal string or an exact integer, not a JSON float")
    if len(str(value)) > 120:
        raise ValueError("decimal representation is too long")
    try:
        result = Decimal(value)
    except Exception as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite() or not 0 < result < MAX_INPUT:
        raise ValueError("must be finite, positive and less than 1e24")
    _, digits, exponent = result.as_tuple()
    while digits and digits[-1] == 0:
        digits = digits[:-1]
        exponent += 1
    if exponent < -18:
        raise ValueError("at most 18 fractional places are supported")
    return result


class ConflictError(Exception):
    def __init__(self, code: str, message: str, **details):
        self.code, self.message, self.details = code, message, details
        super().__init__(message)


def check_capacity(quantity: Decimal, *balances: Decimal) -> None:
    if quantity > MAX_QUANTITY or any(abs(value) >= MAX_BALANCE for value in balances):
        raise ConflictError("position_limit", "position exceeds supported numeric capacity")
