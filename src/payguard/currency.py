"""Currency conversion to the scoring base (USD).

Every risk number in the platform is in USD: the card model was trained on USD amounts, and the rail
scorecards, the Travel Rule threshold and the expected-loss policy are all USD. A payment in another
currency is converted here before it is scored, so ₦46,500 is scored as ~$30, not as $46,500. The caller's
own `amount_usd` (rail payments) always wins over our reference rate.

Crypto assets have no configured rate (prices move by the minute), so a crypto payment must carry
`amount_usd`; without it the payment is rejected rather than scored as if 1 BTC were $1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


class CurrencyError(ValueError):
    """The payment's amount cannot be expressed in USD (unknown currency, or crypto without amount_usd)."""

    def __init__(self, code: str, message: str, currency: str):
        super().__init__(message)
        self.code, self.message, self.currency = code, message, currency


@dataclass(frozen=True)
class Converted:
    currency: str  # as submitted (normalised to upper case)
    amount: float  # as submitted, in `currency`
    amount_usd: float  # what risk is computed on
    fx_rate: float | None  # units of `currency` per USD that we applied; None when the caller gave amount_usd


@dataclass
class FxTable:
    rates: dict[str, float]  # units per 1 USD
    base: str = "USD"
    as_of: str | None = None
    source: str | None = None
    display: list[str] = field(default_factory=lambda: ["USD"])

    @classmethod
    def load(cls, path: Path | None, overrides: dict[str, float] | None = None) -> "FxTable":
        cfg = yaml.safe_load(path.read_text()) if path and path.exists() else {}
        rates = {"USD": 1.0, **{k.upper(): float(v) for k, v in (cfg.get("rates") or {}).items()}}
        rates.update({k.upper(): float(v) for k, v in (overrides or {}).items()})
        bad = [k for k, v in rates.items() if not v > 0]
        if bad:
            raise ValueError(f"FX rates must be positive: {bad}")
        display = [c.upper() for c in cfg.get("display", ["USD"]) if c.upper() in rates]
        return cls(rates, cfg.get("base", "USD"), cfg.get("as_of"), cfg.get("source"), display or ["USD"])

    def supports(self, currency: str) -> bool:
        return currency.upper() in self.rates

    def to_usd(self, amount: float, currency: str) -> float:
        cur = currency.upper()
        if cur not in self.rates:
            raise CurrencyError("unsupported_currency", f"currency {cur} is not supported (supported: "
                                f"{', '.join(sorted(self.rates))})", cur)
        return amount / self.rates[cur]

    def from_usd(self, amount_usd: float, currency: str) -> float | None:
        cur = currency.upper()
        return amount_usd * self.rates[cur] if cur in self.rates else None

    def convert(self, payment) -> Converted:
        """USD amount for any payment. Card payments have no amount_usd field; rail payments may carry one."""
        cur = (payment.currency or "").upper()
        given = getattr(payment, "amount_usd", None)
        if given is not None:
            return Converted(cur, payment.amount, float(given), None)
        if cur not in self.rates:
            if getattr(payment, "rail", "card") == "crypto":
                raise CurrencyError("amount_usd_required", f"amount_usd is required for {cur or 'crypto'} payments "
                                    "(no reference rate is configured for crypto assets)", cur)
            self.to_usd(payment.amount, cur)  # raises unsupported_currency
        return Converted(cur, payment.amount, payment.amount / self.rates[cur], self.rates[cur])

    def public(self) -> dict:
        return {"base": self.base, "rates": dict(self.rates), "display": list(self.display), "as_of": self.as_of,
                "source": self.source}
