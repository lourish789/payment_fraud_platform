"""Proof-of-payment receipt verification.

    image -> OCR (RapidOCR, ONNX, CPU) -> field extraction -> ledger reconciliation -> image forensics

Ledger reconciliation is the primary control: a receipt is only evidence if the transfer it shows
exists in *our* records with the same amount and date. Forensics (edge-normalised Error Level
Analysis) is the fallback for when the ledger cannot be consulted (e.g. inbound transfers from
another bank still pending), and an extra signal otherwise.

Verdicts: verified | mismatch | not_found | suspected_tampering | unreadable
"""

from __future__ import annotations

import io
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops

LedgerLookup = Callable[[str], dict | None]
VALUE_ROWS = ["Amount", "Reference", "Date", "Sender", "Beneficiary", "Account", "Narration"]
CHECKED_ROWS = ["Amount", "Date", "Reference"]
MAX_PIXELS = 4_000_000

_AMOUNT = re.compile(r"([\d][\d,]*(?:\.\d{1,2})?)")
_DATE = re.compile(r"(\d{1,2})\s*([A-Za-z]{3})\s*(\d{4})\s*(\d{1,2}):?(\d{2})")

DEFAULT_THRESHOLDS = {"ela_low": 0.75, "ela_high": 1.30}  # overwritten by `payguard vision-eval` calibration


@dataclass
class OcrBox:
    text: str
    box: tuple[float, float, float, float]
    conf: float


class _Ocr:
    _engine = None
    _lock = threading.Lock()

    @classmethod
    def run(cls, img: Image.Image) -> list[OcrBox]:
        with cls._lock:  # onnxruntime session is shared; RapidOCR is not documented as thread-safe
            if cls._engine is None:
                from rapidocr_onnxruntime import RapidOCR

                cls._engine = RapidOCR()
            res, _ = cls._engine(np.asarray(img))
        out = []
        for pts, text, conf in res or []:
            xs, ys = [p[0] for p in pts], [p[1] for p in pts]
            out.append(OcrBox(text, (min(xs), min(ys), max(xs), max(ys)), float(conf)))
        return out


# OCR routinely swaps these glyph pairs in alphanumeric references (found in evaluation: 'VEN0' read as 'VENO').
_CONFUSABLE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "S": "5", "B": "8", "Z": "2", "G": "6"})


def ocr_key(ref: str) -> str:
    """Canonical form for comparing references across OCR confusions."""
    return re.sub(r"[^A-Z0-9]", "", ref.upper()).translate(_CONFUSABLE)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s).lower()


def extract_fields(boxes: list[OcrBox]) -> dict[str, dict]:
    """Label/value pairs from a two-column layout: a value is the text right of its label on the same line."""
    fields: dict[str, dict] = {}
    for label in VALUE_ROWS:
        lab = next((b for b in boxes if _norm(b.text).startswith(_norm(label))), None)
        if lab is None:
            continue
        cy = (lab.box[1] + lab.box[3]) / 2
        same_row = [b for b in boxes if b is not lab and abs((b.box[1] + b.box[3]) / 2 - cy) < 18 and b.box[0] > lab.box[0]]
        if same_row:
            text = " ".join(b.text for b in sorted(same_row, key=lambda b: b.box[0]))
            x0 = min(b.box[0] for b in same_row); y0 = min(b.box[1] for b in same_row)
            x1 = max(b.box[2] for b in same_row); y1 = max(b.box[3] for b in same_row)
            fields[label] = {"text": text, "box": (x0, y0, x1, y1)}
        elif len(lab.text) > len(label) + 2:  # OCR merged label and value into one box
            fields[label] = {"text": lab.text[len(label):].lstrip(": "), "box": lab.box}
    return fields


def parse_amount(text: str) -> float | None:
    m = _AMOUNT.search(text.replace(" ", ""))
    try:
        return float(m.group(1).replace(",", "")) if m else None
    except ValueError:
        return None


def parse_date(text: str) -> datetime | None:
    m = _DATE.search(text)
    if not m:
        return None
    d, mon, y, hh, mm = m.groups()
    try:
        return datetime.strptime(f"{d} {mon.title()} {y} {hh}:{mm}", "%d %b %Y %H:%M")
    except ValueError:
        return None


def ela_map(img: Image.Image, quality: int = 90) -> np.ndarray:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    buf.seek(0)
    diff = ImageChops.difference(img, Image.open(buf).convert("RGB"))
    return np.asarray(diff, dtype=np.float32).mean(axis=2)


def forensics(img: Image.Image, fields: dict[str, dict], thresholds: dict) -> dict:
    """Error Level Analysis, normalised per text-edge pixel.

    JPEG error concentrates on edges, so raw ELA is higher wherever text is denser. Dividing the ELA
    energy of a field by its number of edge pixels makes fields comparable; a field that was pasted in
    after the image's first compression has a different error level per edge than untouched fields."""
    ela = ela_map(img)
    gray = np.asarray(img.convert("L"), dtype=np.float32)
    gy, gx = np.gradient(gray)
    edges = np.hypot(gx, gy) > 40
    stats = {}
    for row, f in fields.items():
        x0, y0, x1, y1 = (int(round(v)) for v in f["box"])
        sl = (slice(max(y0, 0), max(y1, y0 + 1)), slice(max(x0, 0), max(x1, x0 + 1)))
        n_edge = int(edges[sl].sum())
        if n_edge >= 30:
            stats[row] = float(ela[sl][edges[sl]].mean())
    out = {}
    for row in ("Amount", "Date"):
        others = [v for k, v in stats.items() if k not in CHECKED_ROWS]  # fields forgers rarely touch
        if row not in stats or len(others) < 2:
            continue
        ratio = stats[row] / (float(np.median(others)) + 1e-3)
        # two-sided: re-saving at a lower quality than the original pushes an edit *below* the band
        out[row] = {"ela_edge_ratio": round(ratio, 3),
                    "flag": not (thresholds["ela_low"] <= ratio <= thresholds["ela_high"])}
    return out


class ReceiptVerifier:
    def __init__(self, thresholds_path: Path | None = None):
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds_path and thresholds_path.exists():
            self.thresholds.update(json.loads(thresholds_path.read_text()))

    def verify(self, image_bytes: bytes, ledger: LedgerLookup | None, claimed_reference: str | None = None) -> dict:
        img = Image.open(io.BytesIO(image_bytes))
        if img.width * img.height > MAX_PIXELS:  # bound CPU/memory per request
            scale = (MAX_PIXELS / (img.width * img.height)) ** 0.5
            img = img.resize((int(img.width * scale), int(img.height * scale)))
        img = img.convert("RGB")
        boxes = _Ocr.run(img)
        fields = extract_fields(boxes)
        amount = parse_amount(fields["Amount"]["text"]) if "Amount" in fields else None
        ref = re.sub(r"[^A-Z0-9-]", "", fields["Reference"]["text"].upper()) if "Reference" in fields else None
        when = parse_date(fields["Date"]["text"]) if "Date" in fields else None
        extracted = {"amount": amount, "reference": ref, "date": when.isoformat() if when else None,
                     "raw": {k: v["text"] for k, v in fields.items()}}
        checks: dict = {"ocr_boxes": len(boxes)}
        forensic = forensics(img, fields, self.thresholds)
        checks["forensics"] = forensic
        tampered = any(v["flag"] for v in forensic.values())

        if amount is None or ref is None:
            return self._result("unreadable", extracted, checks, claimed_reference)
        if claimed_reference:
            if ocr_key(ref) != ocr_key(claimed_reference):
                checks["claimed_reference_matches"] = False
                return self._result("mismatch", extracted, checks, claimed_reference,
                                    note="receipt shows a different reference than the one claimed")
            ref = claimed_reference.upper()  # same up to OCR confusables: look up the exact claimed string
        if ledger is not None:
            tx = ledger(ref)
            checks["ledger"] = "found" if tx else "not_found"
            if tx is None:
                return self._result("not_found", extracted, checks, claimed_reference)
            checks["amount_matches"] = abs(tx["amount"] - amount) <= 0.011
            checks["date_matches"] = when is not None and abs((tx["time"].replace(tzinfo=None) - when).total_seconds()) <= 120
            if not (checks["amount_matches"] and checks["date_matches"]):
                return self._result("mismatch", extracted, checks, claimed_reference)
            # Reference, amount and date all match our own records: the receipt shows a real transfer as it
            # happened. A pixel anomaly cannot change that (an "edit" that matches the truth gains a forger
            # nothing), so it is surfaced for the analyst but does not override the ledger.
            checks["forensic_anomaly"] = tampered
            return self._result("verified", extracted, checks, claimed_reference)
        checks["ledger"] = "unavailable"
        if tampered:  # forensics decide only when the ledger cannot be consulted
            return self._result("suspected_tampering", extracted, checks, claimed_reference)
        return self._result("verified", extracted, checks, claimed_reference,
                            note="no ledger record available; verified on image forensics only")

    @staticmethod
    def _result(verdict, extracted, checks, claimed, note=None) -> dict:
        r = {"verdict": verdict, "extracted": extracted, "checks": checks, "claimed_reference": claimed}
        if note:
            r["note"] = note
        return r
