"""Customer purchase-order PDF parsers.

Each retailer emails its purchase orders in its own layout. A parser
recognises one layout (`detect`) and turns its text into a ParsedOrder
(`parse`). Add a new retailer by writing another parser class and
appending it to PARSERS.

Parsers only READ the document. Matching lines to our stock, checking
prices and totals against Evolution happens in services.order_import.validate.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class ParsedLine(BaseModel):
    line_no: int
    # "Supplier Code" column: the retailer's record of OUR stock code.
    supplier_code: str = ""
    # "Item" column: the retailer's own article number / barcode.
    item_code: str = ""
    # Barcode (EAN), when the layout prints one separately (Pick n Pay).
    barcode: str = ""
    description: str = ""
    qty: float = 0.0              # units: what goes on our order line
    # Packs x pack size, when the layout orders in packs (Pick n Pay):
    # qty = pack_qty x pack_size. None when the layout prints units.
    pack_size: Optional[float] = None
    pack_qty: Optional[float] = None
    unit_excl: float = 0.0        # UnitCost (Excl); 0 when not printed
    # UnitCost (Incl). Not always a whole cent: Pick n Pay prints only the
    # line total, so it's line_incl / qty (e.g. 142.84 / 12 = 11.9033).
    unit_incl: float = 0.0
    vat_rate: float = 0.0         # derived from the line's VAT / Exclusive
    # False when the layout prints no VAT (Pick n Pay): the line's VAT check
    # is skipped and the excl figures are worked out from the item's VAT.
    vat_known: bool = True
    # True when the layout prints EXCL prices only (OK Foods): unit_incl /
    # line_incl stay 0 here and validation works them out from the item's
    # VAT in Evolution.
    prices_excl: bool = False
    line_excl: float = 0.0
    line_vat: float = 0.0
    line_incl: float = 0.0
    # Set when the line's own maths doesn't add up (qty x price vs amount),
    # which usually means a column was misread.
    parse_warning: Optional[str] = None


class ParsedOrder(BaseModel):
    format_name: str
    retailer: str
    order_no: str = ""
    # True when the user typed the PO number (it wasn't on the document).
    order_no_typed: bool = False
    order_date: Optional[date] = None
    delivery_from: Optional[date] = None
    delivery_to: Optional[date] = None
    # Store identifier printed on the order. Checked against
    # Client.ucARStoreCode for the chosen customer.
    store_code: str = ""
    store_name: str = ""
    lines: List[ParsedLine] = []
    total_qty: Optional[float] = None
    total_excl: Optional[float] = None
    total_vat: Optional[float] = None
    total_incl: Optional[float] = None
    warnings: List[str] = []
    # The uploaded PDF as stored in _tb_SOImportDocument (set by /parse).
    document_id: Optional[int] = None


class OrderParseError(Exception):
    """The file couldn't be read or isn't a layout we recognise."""


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_NUM_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")


def _is_num(tok: str) -> bool:
    return bool(_NUM_RE.match(tok))


def _num(tok: str) -> float:
    return float(tok.replace(",", ""))


def _dmy(s: Optional[str], fmt: str = "%d/%m/%Y") -> Optional[date]:
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), fmt).date()
    except ValueError:
        return None


# "1 437,60" / "1437,60" / "96,00": comma decimals, space (or no-break
# space) between thousands.
_ZA_AMOUNT = r"\d{1,3}(?:[ \u00a0]\d{3})+,\d{2}|\d+,\d{2}"


def _za_num(tok: str) -> float:
    return float(re.sub(r"[ \u00a0]", "", tok).replace(",", "."))


def _search(pattern: str, text: str, flags: int = 0) -> Optional[str]:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def extract_pdf_pages(data: bytes) -> List[str]:
    """Text of each page, in order. Raises OrderParseError for anything
    that isn't a readable, text-based PDF."""
    try:
        import pdfplumber
    except ImportError as e:  # pragma: no cover - deployment problem
        raise OrderParseError(
            "PDF support isn't installed on the server (pip install pdfplumber)."
        ) from e

    if not data.startswith(b"%PDF"):
        raise OrderParseError("The file is not a PDF.")
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            pages = [(p.extract_text() or "") for p in pdf.pages]
    except Exception as e:
        raise OrderParseError(f"Could not read the PDF: {e}") from e
    if not any(p.strip() for p in pages):
        raise OrderParseError(
            "The PDF has no readable text (it may be a scanned image). "
            "Capture this order manually."
        )
    return pages


# ---------------------------------------------------------------------------
# Boxer Superstores
# ---------------------------------------------------------------------------

class BoxerParser:
    """Boxer Superstores purchase order (one table, columns:
    Deal No | Supplier Code | Item | Description | Variant | Order Pack |
    Size | Pack | Vat Rate | PackCost (Incl) | UnitCost (Excl) |
    UnitCost (Incl) | Pack Weight | Extended Weight | Qty (Pack) |
    Qty (Unit) | Free | Exclusive | Vat | Inclusive).

    Text extraction glues the Item number onto the description
    ("41157016Hygienik ...") and wraps long descriptions onto extra lines,
    so rows are read as: a row-start line (deal no, supplier code, 8-digit
    item) followed by continuation lines, until the next row start.

    The 11 right-most columns are always numeric and always present
    (Vat Rate is blank on zero-rated lines, so it's NOT used — the rate is
    derived from the line's VAT / Exclusive instead).
    """

    name = "BOXER"
    retailer = "Boxer Superstores"

    # deal no, optional supplier code, 8-digit item number, rest of line
    _ROW_START = re.compile(
        r"^\s*(\d{1,8})\s+(?:(\S{1,20})\s+)?(\d{8})(?!\d)\s*(.*)$"
    )
    _TAIL_LEN = 11  # PackCost .. Inclusive

    @staticmethod
    def detect(text: str) -> bool:
        return "Boxer Superstores" in text and "PURCHASE ORDER" in text.upper()

    def parse(self, pages: List[str]) -> ParsedOrder:
        full = "\n".join(pages)
        first = pages[0]

        order_no = _search(r"Order No:\s*(\S+)", first) or ""
        order = ParsedOrder(
            format_name=self.name,
            retailer=self.retailer,
            order_no=order_no,
            order_date=_dmy(_search(r"Order Date:\s*(\d{2}/\d{2}/\d{4})", first)),
            store_code=_search(r"SAP Branch:\s*(\S+)", first) or "",
            store_name=_search(r"(?<!SAP )Branch:\s*([^\n]+?)\s*(?:\n|$)", first) or "",
        )

        # Delivery window: the row under "Purchase Order | Delivery From |
        # Delivery To" reads "<order no> <from> <to>".
        if order_no:
            m = re.search(
                re.escape(order_no) + r"\s+(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})",
                full,
            )
            if m:
                order.delivery_from = _dmy(m.group(1))
                order.delivery_to = _dmy(m.group(2))

        order.lines = self._parse_lines(full)
        self._parse_totals(full, order)

        if not order.store_code:
            order.warnings.append("SAP Branch (store code) not found on the document.")
        if not order.lines:
            order.warnings.append("No order lines could be read from the document.")
        if order.total_incl is None:
            order.warnings.append("Order total (Gross Total) not found on the document.")
        return order

    # -- lines --------------------------------------------------------------

    def _parse_lines(self, full: str) -> List[ParsedLine]:
        rows: List[dict] = []
        current: Optional[dict] = None
        in_table = False

        for raw in full.splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("Deal No"):
                in_table = True
                current = None
                continue
            if not in_table:
                continue
            # End of the table on this page (totals, footer, page break).
            if (
                line.startswith(("Sub Total", "Gross Total", "Less Allowance",
                                 "Add Transport", "Comments:"))
                or re.match(r"^Page\s*\d", line)
            ):
                in_table = False
                current = None
                continue

            m = self._ROW_START.match(line)
            if m:
                current = {
                    "supplier_code": (m.group(2) or "").strip(),
                    "item_code": m.group(3),
                    "text": m.group(4) or "",
                }
                rows.append(current)
            elif current is not None:
                current["text"] += " " + line

        return [self._build_line(i, r) for i, r in enumerate(rows, start=1)]

    def _build_line(self, line_no: int, row: dict) -> ParsedLine:
        tokens = row["text"].split()

        # Last run of consecutive numeric tokens long enough to hold the
        # numeric tail. Anything after it is a wrapped description.
        best: Optional[tuple] = None
        i = 0
        while i < len(tokens):
            if _is_num(tokens[i]):
                j = i
                while j < len(tokens) and _is_num(tokens[j]):
                    j += 1
                if j - i >= self._TAIL_LEN:
                    best = (i, j)
                i = j
            else:
                i += 1

        out = ParsedLine(
            line_no=line_no,
            supplier_code=row["supplier_code"],
            item_code=row["item_code"],
        )

        if best is None:
            out.description = " ".join(tokens)
            out.parse_warning = "Could not read the quantity / price columns of this line."
            return out

        start, end = best
        tail = [_num(t) for t in tokens[end - self._TAIL_LEN:end]]
        (_pack_cost, unit_excl, unit_incl, _pack_w, _ext_w,
         _qty_pack, qty_unit, _free, line_excl, line_vat, line_incl) = tail

        # Description = everything outside the numeric run (the run also
        # holds the VAT rate column when it's filled in), minus the
        # "<order pack> Case" columns that sit just before the numbers.
        desc = " ".join(tokens[:start] + tokens[end:])
        desc = re.sub(r"\s+\d+\s+Case\b", "", desc).strip()

        out.description = desc
        out.qty = qty_unit
        out.unit_excl = unit_excl
        out.unit_incl = unit_incl
        out.line_excl = line_excl
        out.line_vat = line_vat
        out.line_incl = line_incl
        out.vat_rate = round(line_vat / line_excl * 100, 1) if line_excl else 0.0

        # Self-consistency: catches a column that was misread.
        problems = []
        if abs(qty_unit * unit_excl - line_excl) > max(0.05, abs(line_excl) * 0.001):
            problems.append("Qty x UnitCost (Excl) does not equal the Exclusive amount")
        if abs(line_excl + line_vat - line_incl) > 0.02:
            problems.append("Exclusive + VAT does not equal the Inclusive amount")
        if problems:
            out.parse_warning = "; ".join(problems) + " on the document."
        return out

    # -- totals -------------------------------------------------------------

    def _parse_totals(self, full: str, order: ParsedOrder) -> None:
        # Gross Total is the payable figure (after allowance / transport);
        # fall back to Sub Total.
        for label in ("Gross Total:", "Sub Total:"):
            for raw in full.splitlines():
                if label not in raw:
                    continue
                nums = [_num(t) for t in raw.split(label, 1)[1].split() if _is_num(t)]
                if len(nums) >= 5:
                    order.total_qty = nums[0]
                    order.total_excl, order.total_vat, order.total_incl = nums[-3:]
                    return
                if len(nums) >= 3:
                    order.total_excl, order.total_vat, order.total_incl = nums[-3:]
                    return


# ---------------------------------------------------------------------------
# Pick n Pay
# ---------------------------------------------------------------------------

class PicknPayParser:
    """Pick n Pay purchase order (columns: Vendor Product Code | PnP Article
    Number | Barcode Number | Description | Cost Price (Incl Tax) | Pack Size
    | Quantity).

    * Vendor Product Code = OUR stock code (matched like Boxer's Supplier
      Code); PnP Article Number = their item code.
    * Quantity is in PACKS: units ordered = Quantity x Pack Size.
    * Cost Price (Incl Tax) is the LINE total incl (the column sums to the
      Total). There's no unit price and no VAT column: the unit incl is
      line total / units.
    * Amounts use comma decimals and space thousands ("1 437,60").
    * Store: "PnP Delivery Site: NC46 - PnP EM Lebo Mall" -> NC46.
    Text extraction may drop the spaces in labels ("PnPDeliverySite:"), so
    label patterns allow for that. Long descriptions wrap onto the next
    line ("... CHOCOLATE" / "350ML").
    """

    name = "PICKNPAY"
    retailer = "Pick n Pay"

    # Row start: code, 4-10 digit article number, 8-14 digit barcode.
    _ROW_START = re.compile(r"^\s*(\S{1,20})\s+(\d{4,10})\s+(\d{8,14})(?!\d)\s*(.*)$")
    # The numbers: line incl, pack size, quantity (packs). Searched anywhere
    # in the row's text, because a wrapped description can come before or
    # after them.
    _NUMS = re.compile(
        r"(?:^|\s)(" + _ZA_AMOUNT + r")\s+(\d+)\s+(\d+)(?=\s|$)"
    )

    @staticmethod
    def detect(text: str) -> bool:
        return bool(re.search(r"PnP\s*Article", text)) and bool(
            re.search(r"PnP\s*Delivery\s*Site", text)
        )

    def parse(self, pages: List[str]) -> ParsedOrder:
        full = "\n".join(pages)
        first = pages[0]

        site = re.search(
            r"PnP\s*Delivery\s*Site:\s*([A-Za-z0-9]+)\s*-\s*([^\n]*)", first
        )
        delivery = _dmy(
            _search(r"Delivery\s*Date:\s*(\d{2}-\d{2}-\d{4})", first), "%d-%m-%Y"
        )
        order = ParsedOrder(
            format_name=self.name,
            retailer=self.retailer,
            order_no=_search(r"PO\s*Number:\s*(\d+)", first) or "",
            order_date=_dmy(
                _search(r"Order\s*Date:\s*(\d{2}-\d{2}-\d{4})", first), "%d-%m-%Y"
            ),
            delivery_from=delivery,
            delivery_to=delivery,
            store_code=site.group(1).strip() if site else "",
            store_name=site.group(2).strip() if site else "",
        )

        order.lines = self._parse_lines(full)
        m = re.search(r"Total:\s*(?:ZAR)?\s*(" + _ZA_AMOUNT + r")", full)
        if m:
            order.total_incl = _za_num(m.group(1))

        if not order.store_code:
            order.warnings.append("PnP Delivery Site (store code) not found on the document.")
        if not order.lines:
            order.warnings.append("No order lines could be read from the document.")
        if order.total_incl is None:
            order.warnings.append("Order total not found on the document.")
        return order

    def _parse_lines(self, full: str) -> List[ParsedLine]:
        rows: List[dict] = []
        current: Optional[dict] = None
        in_table = False
        for raw in full.splitlines():
            line = raw.strip()
            if not line:
                continue
            # Table header (repeats on every page) starts the rows.
            if re.search(r"Barcode\s*Number", line):
                in_table, current = True, None
                continue
            if not in_table:
                continue
            # End of the table / top of the next page.
            if (re.match(r"^Total:", line)
                    or re.search(r"Purchase\s*Order|Order\s*page|Print\s*date", line)):
                in_table, current = False, None
                continue
            m = self._ROW_START.match(line)
            if m:
                current = {"code": m.group(1), "article": m.group(2),
                           "barcode": m.group(3), "text": m.group(4) or ""}
                rows.append(current)
            elif current is not None:
                current["text"] += " " + line
        return [self._build_line(i, r) for i, r in enumerate(rows, start=1)]

    def _build_line(self, line_no: int, row: dict) -> ParsedLine:
        text_ = " ".join(row["text"].split())
        out = ParsedLine(
            line_no=line_no,
            supplier_code=row["code"].strip(),
            item_code=row["article"],
            barcode=row["barcode"],
            vat_known=False,
        )
        found = list(self._NUMS.finditer(text_))
        if not found:
            out.description = text_
            out.parse_warning = "Could not read the price / pack size / quantity of this line."
            return out
        m = found[-1]
        out.description = (text_[:m.start()] + " " + text_[m.end():]).strip()
        out.description = " ".join(out.description.split())
        line_incl = _za_num(m.group(1))
        pack_size, pack_qty = float(m.group(2)), float(m.group(3))
        units = pack_size * pack_qty
        out.qty = units
        out.pack_size = pack_size
        out.pack_qty = pack_qty
        out.line_incl = line_incl
        out.unit_incl = round(line_incl / units, 4) if units else 0.0
        if units <= 0:
            out.parse_warning = "Pack size or quantity is zero on the document."
        return out


# ---------------------------------------------------------------------------
# OK Foods (OK Express / OK Grocer / OK Minimark)
# ---------------------------------------------------------------------------

# "6 813.25" / "1261.92": DOT decimals, space between thousands.
_OK_AMOUNT = r"\d{1,3}(?:[ \u00a0]\d{3})+\.\d{2}|\d+\.\d{2}"


def _ok_num(tok: str) -> float:
    return float(re.sub(r"[ \u00a0]", "", tok))


class OKParser:
    """OK Foods purchase order, "Purchase Order (Placed)" (columns: Product
    Code | Your Code | Description | Pack Size | Order Qty | Bonus Qty |
    Contract Nr | Start Date | Stop Date | List Price | Trade | Disc1 |
    Disc2 | Disc3 | Total Excl).

    * Your Code = OUR stock code. "L152" is a case (Pack Size 10) of 152:
      it's mapped once per corporate group like any other unknown code.
    * Order Qty is in PACKS: units = Order Qty x Pack Size.
    * Prices are EXCL VAT and there's no VAT per line. Total Excl is the
      line amount (List Price is rounded: 12 x 10.42 shows 125.01), so the
      unit excl = Total Excl / units; validation adds the item's VAT
      (prices_excl).
    * No PO number we know of (orders are captured with an "ND..." number
      that isn't on the PDF): order_no stays blank and the user types it.
    * No store code on the PDF (it's in the sender's e-mail address, e.g.
      2545@...): the user picks the customer; the store check is skipped.
    * Dates: "Order 2026/11/06 10:04" and "Deliver Bef 2026/11/09 23:59".
    * The footer's labels and amounts come out of the PDF in any order, so
      the totals are found by their arithmetic (excl + tax = total).
    """

    name = "OK"
    retailer = "OK Foods"

    # Row start: 8-14 digit barcode (Product Code), Your Code, rest.
    _ROW_START = re.compile(r"^\s*(\d{8,14})\s+(\S{1,12})\s+(.*)$")
    # Pack size, order qty, bonus qty, contract nr, start, stop, list price,
    # trade %, disc1 %, disc2 %, disc3, total excl. Searched in the row's
    # joined text: a wrapped description can put them on a later line.
    _NUMS = re.compile(
        r"(?:^|\s)(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\d+(?:\.\d+)?)\s+(\S+)\s+"
        r"(\d{2}/\d{2}/\d{4})\s+(\d{2}/\d{2}/\d{4})\s+(" + _OK_AMOUNT + r")\s+"
        r"(-?[\d.]+)%\s+(-?[\d.]+)%\s+(-?[\d.]+)%\s+(-?[\d.]+)\s+(" + _OK_AMOUNT + r")(?=\s|$)"
    )
    _END = re.compile(
        r"^(?:Arch\s*-\s*Version|Name\s*\(Print|Item\s*Count|User\s*entered|"
        r"Sub\s*Total|Tax:|Total:|Signature|Accept|Page\s+\d)"
    )

    @staticmethod
    def detect(text: str) -> bool:
        return bool(re.search(r"Your\s*Code", text)) and bool(
            re.search(r"Deliver\s*Bef", text)
        ) and bool(re.search(r"Purchase\s*Order", text))

    def parse(self, pages: List[str]) -> ParsedOrder:
        full = "\n".join(pages)
        first = pages[0]

        order_date = _dmy(
            _search(r"\bOrder\s+(\d{4}/\d{2}/\d{2})", first)
            or _search(r"\bDate\s+(\d{4}/\d{2}/\d{2})", first),
            "%Y/%m/%d",
        )
        delivery = _dmy(_search(r"Deliver\s*Bef\.?\s+(\d{4}/\d{2}/\d{2})", first), "%Y/%m/%d")
        order = ParsedOrder(
            format_name=self.name,
            retailer=self.retailer,
            order_no="",
            order_date=order_date,
            delivery_from=delivery,
            delivery_to=delivery,
        )
        order.lines = self._parse_lines(full)
        self._parse_totals(full, order)

        if not order.lines:
            order.warnings.append("No order lines could be read from the document.")
        if order.total_excl is None or order.total_incl is None:
            order.warnings.append("Order totals (Sub Total / Total) not found on the document.")
        if order.delivery_from is None:
            order.warnings.append("Delivery date (Deliver Bef) not found on the document.")
        return order

    def _parse_lines(self, full: str) -> List[ParsedLine]:
        rows: List[dict] = []
        current: Optional[dict] = None
        for raw in full.splitlines():
            line = raw.strip()
            if not line:
                continue
            if self._END.match(line):
                current = None
                continue
            m = self._ROW_START.match(line)
            # A row starts with a barcode followed by more text (the page
            # barcode numbers stand alone on their line).
            if m and m.group(3):
                current = {"barcode": m.group(1), "code": m.group(2), "text": m.group(3)}
                rows.append(current)
            elif current is not None:
                current["text"] += " " + line
        return [self._build_line(i, r) for i, r in enumerate(rows, start=1)]

    def _build_line(self, line_no: int, row: dict) -> ParsedLine:
        text_ = " ".join(row["text"].split())
        out = ParsedLine(
            line_no=line_no,
            supplier_code=row["code"].strip(),
            item_code=row["barcode"],
            barcode=row["barcode"],
            vat_known=False,
            prices_excl=True,
        )
        found = list(self._NUMS.finditer(text_))
        if not found:
            out.description = text_
            out.parse_warning = "Could not read the pack size / quantity / price of this line."
            return out
        m = found[-1]
        out.description = " ".join((text_[:m.start()] + " " + text_[m.end():]).split())
        pack_size, pack_qty, bonus = float(m.group(1)), float(m.group(2)), float(m.group(3))
        line_excl = _ok_num(m.group(12))
        units = pack_size * pack_qty
        out.qty = units
        if pack_size != 1:
            out.pack_size, out.pack_qty = pack_size, pack_qty
        out.line_excl = line_excl
        out.unit_excl = round(line_excl / units, 4) if units else 0.0
        if units <= 0:
            out.parse_warning = "Pack size or quantity is zero on the document."
        elif bonus:
            out.parse_warning = (
                f"The order has a bonus (free) quantity of {bonus:g} on this line. "
                "Capture this order manually."
            )
        return out

    @staticmethod
    def _parse_totals(full: str, order: ParsedOrder) -> None:
        # Everything after "Item Count": the footer. Its labels and amounts
        # can come out in any order, so: Tax is labelled; Sub Total and
        # Total are the pair with Sub Total + Tax = Total.
        i = full.rfind("Item Count")
        footer = full[i:] if i >= 0 else full
        taxes = [_ok_num(t) for t in re.findall(
            r"(?<!entered )Tax:\s*(" + _OK_AMOUNT + r")", footer)]
        if not taxes:
            return
        tax_v = taxes[-1]
        count = _search(r"Item\s*Count:\s*(" + _OK_AMOUNT + r")", footer)
        amounts = [_ok_num(a) for a in re.findall(_OK_AMOUNT, footer)]
        skip = {tax_v, _ok_num(count) if count else None, 0.0}
        cands = [a for a in amounts if a not in skip]
        for excl in cands:
            for incl in cands:
                if incl > excl and abs(excl + tax_v - incl) <= 0.02:
                    order.total_excl, order.total_vat, order.total_incl = excl, tax_v, incl
                    return


PARSERS = [BoxerParser(), PicknPayParser(), OKParser()]


def parse_order_pdf(data: bytes) -> ParsedOrder:
    pages = extract_pdf_pages(data)
    return parse_order_text(pages)


def parse_order_text(pages: List[str]) -> ParsedOrder:
    full = "\n".join(pages)
    for parser in PARSERS:
        if parser.detect(full):
            return parser.parse(pages)
    raise OrderParseError(
        "This order layout isn't recognised yet. Supported: "
        + ", ".join(p.retailer for p in PARSERS)
        + "."
    )
