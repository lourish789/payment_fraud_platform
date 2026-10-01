"""Sanctions screening of counterparty addresses against the OFAC SDN digital-currency address list.

This is a hard regulatory control, not a model: a hit blocks a withdrawal outright, and freezes a deposit
(which cannot be declined: it is already on-chain) pending a sanctions report.

Normalisation matters. EVM addresses (Ethereum, BSC, Arbitrum, and USDT/USDC on them) are hex and
case-insensitive, so they are lower-cased; Bitcoin-style base58/bech32 and Tron addresses are
case-sensitive and kept as-is (bech32 is lower-cased, as the spec defines it case-insensitive).
Screening is conservative: an address is a hit if it appears on any asset's list, whatever chain the
caller claims, because the same string is sometimes listed under a token rather than its chain.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_EVM = re.compile(r"^0x[0-9a-fA-F]{40}$")
_BECH32 = re.compile(r"^(bc1|ltc1|tb1)[0-9a-z]+$", re.IGNORECASE)


def normalize(address: str) -> str:
    a = address.strip()
    if _EVM.match(a) or _BECH32.match(a):
        return a.lower()
    return a


@dataclass
class ScreeningResult:
    hit: bool
    assets: list[str]
    list_name: str = "OFAC SDN (digital currency addresses)"
    list_fetched_at: str | None = None


class SanctionsScreener:
    def __init__(self, entries: dict[str, set[str]], fetched_at: str | None = None):
        self._by_address: dict[str, list[str]] = {}
        for asset, addresses in entries.items():
            for a in addresses:
                self._by_address.setdefault(normalize(a), []).append(asset)
        self.fetched_at = fetched_at

    @classmethod
    def load(cls, directory: Path) -> "SanctionsScreener":
        entries: dict[str, set[str]] = {}
        for p in sorted(directory.glob("sanctioned_addresses_*.txt")):
            asset = p.stem.rsplit("_", 1)[-1]
            entries[asset] = {line.strip() for line in p.read_text().splitlines() if line.strip()}
        stamp = directory / "fetched_at.txt"
        return cls(entries, stamp.read_text().strip() if stamp.exists() else None)

    def __len__(self) -> int:
        return len(self._by_address)

    def screen(self, address: str) -> ScreeningResult:
        assets = self._by_address.get(normalize(address), [])
        return ScreeningResult(bool(assets), sorted(assets), list_fetched_at=self.fetched_at)
