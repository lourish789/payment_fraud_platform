"""Synthetic bank-transfer receipts (genuine and forged) for testing and evaluating the verifier.

There is no public labelled dataset of forged transfer receipts, so the evaluation set is generated.
Bank names are fictional on purpose. Forgeries follow the common real-world workflow: take a genuine
screenshot (already JPEG-compressed once), paint over a field, type new text in a near-matching font,
and save again - which leaves the edited region with a different compression history (what ELA sees)
and often a slightly different glyph size.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BANKS = ["Zenara Bank", "Harmattan Trust", "Kobo Microfinance", "Lagoon Bank"]  # fictional
FONT_CANDIDATES = {
    "regular": ["C:/Windows/Fonts/arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "bold": ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
    "alt": ["C:/Windows/Fonts/calibri.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf"],
}
W, H = 720, 1000
ROWS = ["Amount", "Reference", "Date", "Sender", "Beneficiary", "Account", "Narration"]


def _font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for p in FONT_CANDIDATES[kind]:
        if Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


@dataclass
class ReceiptFields:
    bank: str
    amount: float
    currency: str
    reference: str
    time: datetime
    sender: str
    beneficiary: str
    account: str
    narration: str

    def row_values(self) -> dict[str, str]:
        return {
            "Amount": f"{self.currency} {self.amount:,.2f}",
            "Reference": self.reference,
            "Date": self.time.strftime("%d %b %Y %H:%M"),
            "Sender": self.sender,
            "Beneficiary": self.beneficiary,
            "Account": self.account,
            "Narration": self.narration,
        }


@dataclass
class Rendered:
    image: Image.Image
    boxes: dict[str, tuple[int, int, int, int]] = field(default_factory=dict)  # row -> value bbox


def render(f: ReceiptFields, value_size: int = 26) -> Rendered:
    img = Image.new("RGB", (W, H), (250, 250, 252))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 120], fill=(18, 64, 120))
    d.text((40, 38), f.bank, font=_font("bold", 40), fill="white")
    d.text((40, 160), "Transfer Successful", font=_font("bold", 34), fill=(22, 130, 60))
    label_font, value_font = _font("regular", 22), _font("regular", value_size)
    boxes = {}
    y = 250
    for row, value in f.row_values().items():
        d.text((40, y), row, font=label_font, fill=(110, 110, 120))
        vx = 300
        d.text((vx, y - 2), value, font=value_font, fill=(20, 20, 30))
        boxes[row] = d.textbbox((vx, y - 2), value, font=value_font)
        d.line([(40, y + 50), (W - 40, y + 50)], fill=(225, 225, 230))
        y += 90
    d.text((40, H - 70), "Thank you for banking with us.", font=_font("regular", 20), fill=(140, 140, 150))
    return Rendered(img, boxes)


def jpeg(img: Image.Image, quality: int) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    return Image.open(buf).convert("RGB")


def forge_field(r: Rendered, row: str, new_value: str, rng: random.Random) -> Image.Image:
    """Paint over one value and retype it, then re-save (the forger's screenshot -> editor -> save flow)."""
    img = r.image.copy()
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = r.boxes[row]
    bg = img.getpixel((x0 - 6, y0 + 2))
    d.rectangle([x0 - 2, y0 - 2, max(x1, x0 + 330) + 2, y1 + 4], fill=bg)
    size = rng.choice([24, 25, 27, 28])  # near-miss font size
    d.text((x0 + rng.randint(-2, 2), y0 - 2 + rng.randint(-2, 2)), new_value,
           font=_font(rng.choice(["alt", "regular"]), size), fill=(20, 20, 30))
    return img


def random_fields(rng: random.Random, reference: str, amount: float, when: datetime, currency: str = "NGN") -> ReceiptFields:
    first = ["Adaeze", "Tunde", "Chinedu", "Aisha", "Emeka", "Funke", "Ibrahim", "Ngozi"]
    last = ["Okafor", "Adeyemi", "Bello", "Eze", "Okonkwo", "Balogun", "Musa", "Nwosu"]
    return ReceiptFields(
        bank=rng.choice(BANKS), amount=amount, currency=currency, reference=reference, time=when,
        sender=f"{rng.choice(first)} {rng.choice(last)}", beneficiary=f"{rng.choice(first)} {rng.choice(last)} Stores",
        account=f"******{rng.randint(1000, 9999)}", narration=rng.choice(["Payment for goods", "Order 55812", "POS top-up"]))


KINDS = ("genuine", "amount_edit", "date_edit", "fabricated_reference", "wrong_transaction")


def make_case(kind: str, ledger: dict[str, dict], rng: random.Random) -> tuple[Image.Image, str]:
    """Returns (image, claimed_reference). `ledger` maps reference -> {amount, time, currency}."""
    ref = rng.choice(list(ledger))
    tx = ledger[ref]
    fields = random_fields(rng, ref, tx["amount"], tx["time"], tx["currency"])
    once = jpeg(render(fields).image, quality=rng.choice([88, 90, 92]))  # the "screenshot"
    if kind == "genuine":
        return once, ref
    if kind in ("amount_edit", "date_edit"):
        r = render(fields)
        r.image = once
        if kind == "amount_edit":
            new = f"{tx['currency']} {tx['amount'] * rng.choice([2, 5, 10, 20]):,.2f}"
            img = forge_field(r, "Amount", new, rng)
        else:
            new = (tx["time"] - timedelta(days=rng.randint(3, 40))).strftime("%d %b %Y %H:%M")
            img = forge_field(r, "Date", new, rng)
        return jpeg(img, quality=rng.choice([85, 90, 95])), ref
    if kind == "fabricated_reference":
        fake = "TRF" + "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(12))
        fields.reference = fake
        return jpeg(render(fields).image, quality=90), fake
    if kind == "wrong_transaction":  # a real receipt for a different, smaller transfer
        other = rng.choice([k for k in ledger if k != ref])
        o = ledger[other]
        img = jpeg(render(random_fields(rng, other, o["amount"], o["time"], o["currency"])).image, 90)
        return img, ref
    raise ValueError(kind)


def synthetic_ledger(rng: random.Random, n: int = 200, start: datetime | None = None) -> dict[str, dict]:
    start = start or datetime(2018, 5, 1)
    ledger = {}
    for _ in range(n):
        ref = "TRF" + "".join(rng.choice("0123456789ABCDEFGHJKLMNPQRSTUVWXYZ") for _ in range(12))
        ledger[ref] = {"amount": round(rng.choice([rng.uniform(500, 20000), rng.uniform(20000, 900000)]), 2),
                       "time": start + timedelta(minutes=rng.randint(0, 60 * 24 * 30)), "currency": "NGN"}
    return ledger
