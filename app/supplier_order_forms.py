"""Order forms in the layout a supplier expects, filled from the procurement forecast."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import hashlib
from io import BytesIO
import math
import re
from zipfile import ZIP_DEFLATED, ZipFile

import pandas as pd
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


SECTION_BOARDS = "boards"
SECTION_HPL = "hpl"
SECTION_OTHER = "other"
# The user's rule: low priority goods are not proposed. They can still be typed in by hand.
EXCLUDED_ORDER_PRIORITY = "низкий"


@dataclass(frozen=True)
class OrderFormRow:
    code: str
    name: str
    old_code: str = ""
    section: str = SECTION_BOARDS
    # A generic name ("белый") must not pull in unrelated goods.
    match_by_name: bool = True


WQ_SUPPLIER_TERM = "wq"
WQ_BOARD_FORMAT = "2800*1220*18мм"
# Not the list of what to order, which comes from the site: this only tells which
# supplier code and short name belong to a site product.
WQ_ORDER_ROWS: tuple[OrderFormRow, ...] = (
    OrderFormRow("WQ63815M", "Ванильный крем", "US77102/PGR4102"),
    OrderFormRow("WQ63814M", "Молочный пломбир", "US77103/PGR4103"),
    OrderFormRow("WQ63893M", "Маффин", "US77301/PGR4201"),
    OrderFormRow("WQ63845M", "Брауни", "US77302/PGR4202"),
    OrderFormRow("US77304/PGR4204", "Орео", "US77304/PGR4204"),
    OrderFormRow("US77305/PGR4205", "Тирамису", "US77305/PGR4205"),
    OrderFormRow("WQ63864M", "Песочное печенье", "US77202/PGR4302"),
    OrderFormRow("WQ63819M", "Бисквит", "US77306/PGR4206"),
    OrderFormRow("WQ63809M", "Вупи пай", "US77307/PGR4207"),
    OrderFormRow("WQ63812M", "Чизкейк", "US77311/PGR4211"),
    OrderFormRow("WQ63853M", "Меренга", "US77312/PGR4212"),
    OrderFormRow("WQ63811M", "Глазурь", "US77314/PGR4214"),
    OrderFormRow("WQ63894M", "Пряный апельсин", "US77401/PGR4401"),
    OrderFormRow("WQ63851M", "Спелый авокадо", "US77601/PGR4601"),
    OrderFormRow("WQ63167M", "Мятный кекс", "US77602/PGR4602"),
    OrderFormRow("WQ63102M-1", "Ментоловый лёд", "US77603/PGR4603"),
    OrderFormRow("WQ63895M", "Бабл гам", "US77702/PGR4702"),
    OrderFormRow("WQ63850M", "Черничный тарт"),
    OrderFormRow("WQ63098M-1", "Панна кота"),
    OrderFormRow("WQ63839M", "Пьяная вишня"),
    OrderFormRow("WQ63833M", "Моти", "NEW"),
    OrderFormRow("WQ63033M-1", "Туман", "NEW"),
    OrderFormRow("WQ63142LJ", "Титан", "NEW"),
    OrderFormRow("WQ63835M", "Крем Брюле", "NEW"),
    OrderFormRow("WQ63898MJ", "Бронза", "NEW"),
    OrderFormRow("WQ63825MJ", "Бархат", "NEW"),
    OrderFormRow("WQWE71406-2(1386)", "Кассела кор", "NEW"),
    OrderFormRow("WQWE71406-1(1385)", "Кассела натур", "NEW"),
    OrderFormRow("WQWE7140-6-(1367)", "Кассела светл", "NEW"),
    OrderFormRow("WQS31008-MT", "Изысканный Камень", section=SECTION_HPL),
    OrderFormRow("WQS31015-MT", "Мрамор Венато", section=SECTION_HPL),
    OrderFormRow("81050", "белый", section=SECTION_HPL, match_by_name=False),
    OrderFormRow("WQS1837-3 FR", "Молочный Кварцит", section=SECTION_HPL),
    OrderFormRow("WQW47183-1 ELM", "Орех Восточный", section=SECTION_HPL),
    OrderFormRow("WQW47183-2 ELM", "Дерево Песчаное", section=SECTION_HPL),
)

ORDER_FORM_COLUMNS = [
    "row_id",
    "code",
    "quantity",
    "old_code",
    "edge_or_sheets",
    "name",
    "section",
    "abc_class",
    "xyz_class",
    "priority",
    "stock_status",
    "available_stock_qty",
    "forecast_qty",
    "product",
    "edge_product",
    "supplier",
    "low_priority_skipped",
]

_EDGE_SIZE = re.compile(r"(?<![0-9])1\s*[*xх×]\s*22(?![0-9])")
_BOARD_SIZE_PARTS = ("18", "1220", "2800")
_NUMBER = re.compile(r"[0-9]+")
_ARTICLE = re.compile(r"^([0-9A-Za-z][0-9A-Za-z/._()-]*[0-9][0-9A-Za-z/._()-]*)\s+(.+)$")
_SIZE_TAIL = re.compile(r"\s*\d+(?:[.,]\d+)?\s*[*xх×]\s*\d.*$")


def _normalize(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def _contains_term(text: str, term: str) -> bool:
    term = _normalize(term)
    return bool(term) and re.search(rf"(?<![0-9a-zа-я]){re.escape(term)}(?![0-9a-zа-я])", text) is not None


def _is_edge(text: str) -> bool:
    return _EDGE_SIZE.search(text) is not None


def _is_board(text: str) -> bool:
    numbers = set(_NUMBER.findall(text))
    return all(part in numbers for part in _BOARD_SIZE_PARTS)


def _old_code_terms(old_code: str) -> list[str]:
    return [part for part in old_code.split("/") if part and part != "NEW"]


def _product_kind(text: str) -> str:
    if _is_edge(text):
        return "edge"
    if "hpl" in text:
        return "hpl"
    if _is_board(text):
        return "board"
    return ""


def _whole_quantity(values: pd.Series) -> int:
    total = float(pd.to_numeric(values, errors="coerce").fillna(0).clip(lower=0).sum())
    return math.ceil(total - 1e-9) if total > 0 else 0


def is_wq_supplier(value: object) -> bool:
    return _contains_term(_normalize(value), WQ_SUPPLIER_TERM)


def _catalog_row(text: str, kind: str, catalog: tuple[OrderFormRow, ...]) -> OrderFormRow | None:
    """The known supplier code of a site product: by article first, by name otherwise."""
    wanted = SECTION_HPL if kind == "hpl" else SECTION_BOARDS
    candidates = [row for row in catalog if row.section == wanted]
    for row in candidates:
        if any(_contains_term(text, term) for term in (row.code, *_old_code_terms(row.old_code))):
            return row
    for row in candidates:
        if row.match_by_name and _contains_term(text, row.name):
            return row
    return None


def _split_product(product: str, kind: str) -> tuple[str, str]:
    """Article and short name from a site name such as "US77301/PGR4201 Маффин 18*1220*2800мм"."""
    match = _ARTICLE.match(product.strip())
    article, name = (match.group(1), match.group(2)) if match else ("", product.strip())
    if kind in ("board", "edge"):
        name = _SIZE_TAIL.sub("", name).strip() or name
    return article, name


def build_order_form_rows(
    procurement_forecast: pd.DataFrame,
    *,
    only_wq_supplier: bool = True,
    catalog: tuple[OrderFormRow, ...] = WQ_ORDER_ROWS,
) -> pd.DataFrame:
    """One line per position of the supplier: boards with their edge metres, HPL sheets, other goods."""
    if procurement_forecast.empty or "product" not in procurement_forecast.columns:
        return pd.DataFrame(columns=ORDER_FORM_COLUMNS)
    forecast = procurement_forecast
    if only_wq_supplier:
        if "supplier" not in forecast.columns:
            return pd.DataFrame(columns=ORDER_FORM_COLUMNS)
        forecast = forecast[forecast["supplier"].map(is_wq_supplier)]

    grouped: dict[str, dict[str, object]] = {}
    for record in forecast.to_dict(orient="records"):
        product = str(record["product"])
        text = _normalize(product)
        kind = _product_kind(text)
        article, name = _split_product(product, kind)
        known = _catalog_row(text, kind, catalog) if kind else None
        code = known.code if known else article
        section = SECTION_HPL if kind == "hpl" else SECTION_BOARDS if kind in ("board", "edge") else SECTION_OTHER
        # A board and its edge band of one decor share a line; "US77301/PGR4201" and "PGR4201" are one article.
        decor = _normalize(code.split("/")[-1] if code else name)
        row_id = f"{section}:{decor}"
        row = grouped.setdefault(
            row_id,
            {
                "row_id": row_id,
                "code": code,
                "quantity": 0,
                "old_code": (known.old_code if known and known.old_code else article) if article or known else "",
                "edge_or_sheets": 0,
                "name": known.name if known else name,
                "section": section,
                "abc_class": "",
                "xyz_class": "",
                "priority": "",
                "stock_status": "",
                "available_stock_qty": 0.0,
                "forecast_qty": 0.0,
                "product": "",
                "edge_product": "",
                "supplier": record.get("supplier", ""),
                "low_priority_skipped": False,
            },
        )
        recommended = pd.to_numeric(pd.Series([record.get("recommended_order_qty")]), errors="coerce").fillna(0).iloc[0]
        needed = max(float(recommended), 0.0)
        priority = _normalize(record.get("priority", ""))
        if priority == EXCLUDED_ORDER_PRIORITY:
            row["low_priority_skipped"] = bool(row["low_priority_skipped"]) or needed > 0
            needed = 0.0
        needed_whole = math.ceil(needed - 1e-9) if needed > 0 else 0
        if kind == "edge":
            row["edge_or_sheets"] += needed_whole
            row["edge_product"] = row["edge_product"] or product
        elif section == SECTION_BOARDS:
            row["quantity"] += needed_whole
            row["product"] = row["product"] or product
        else:
            row["edge_or_sheets"] += needed_whole
            row["product"] = row["product"] or product
        if kind != "edge" or not row["priority"]:
            for column in ("abc_class", "xyz_class", "priority", "stock_status"):
                row[column] = record.get(column, "") or row[column]
        for column in ("available_stock_qty", "forecast_qty"):
            value = pd.to_numeric(pd.Series([record.get(column)]), errors="coerce").fillna(0).iloc[0]
            if kind != "edge":
                row[column] += float(value)
    rows = pd.DataFrame(list(grouped.values()), columns=ORDER_FORM_COLUMNS)
    section_order = {SECTION_BOARDS: 0, SECTION_HPL: 1, SECTION_OTHER: 2}
    return (
        rows.assign(_section=rows["section"].map(section_order))
        .sort_values(["_section", "name"], kind="stable")
        .drop(columns="_section")
        .reset_index(drop=True)
    )


def order_form_from_items(items: pd.DataFrame) -> pd.DataFrame:
    """The form for a saved order: its own quantities, not the current recommendation."""
    return build_order_form_rows(items.rename(columns={"quantity": "recommended_order_qty"}), only_wq_supplier=False)


def order_items_from_form(order_rows: pd.DataFrame) -> pd.DataFrame:
    """Order lines for the form as it stands, so the ordered goods count as in transit."""
    quantities: dict[str, float] = {}
    for record in order_rows.to_dict(orient="records"):
        # A position without sales has no site product yet: name it so that it
        # is not lost and a saved order can rebuild the same form.
        label = f"{record['code']} {record['name']}".strip()
        if record["section"] != SECTION_BOARDS:
            prefix = "HPL " if record["section"] == SECTION_HPL else ""
            lines = ((record["product"] or f"{prefix}{label}", record["edge_or_sheets"]),)
        else:
            lines = (
                (record["product"] or f"{label} 18*1220*2800мм", record["quantity"]),
                (record["edge_product"] or f"{label} 1*22мм", record["edge_or_sheets"]),
            )
        for product, quantity in lines:
            if float(quantity) > 0:
                quantities[product] = quantities.get(product, 0.0) + float(quantity)
    return pd.DataFrame(
        [
            {"product": product, "quantity": quantity, "unit_cost": 0.0, "comment": ""}
            for product, quantity in quantities.items()
        ],
        columns=["product", "quantity", "unit_cost", "comment"],
    )


def order_form_supplier(order_rows: pd.DataFrame, default: str = "WQ") -> str:
    """The site's name for this supplier: the one most of the form's goods are assigned to."""
    suppliers = order_rows["supplier"].fillna("").astype(str).str.strip()
    suppliers = suppliers[suppliers.ne("") & suppliers.str.casefold().ne("не назначен")]
    return str(suppliers.mode().iloc[0]) if not suppliers.empty else default


@dataclass
class ParsedOrderFile:
    """Final quantities from a form the user edited and uploaded back."""

    fingerprint: str = ""
    quantities: dict[str, tuple[int, int]] = field(default_factory=dict)
    unknown_codes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _normalize_code(value: object) -> str:
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return "".join(str(value if value is not None else "").casefold().split())


def _file_quantity(value: object) -> int | None:
    """Whole units, rounded up; None when the cell is not a usable number."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        number = float(str(value).replace(",", ".").replace(" ", "")) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or number < 0:
        return None
    return math.ceil(number - 1e-9)


_BOARD_TITLES = ("плиты, шт", "кол-во")
_EDGE_TITLES = ("кромка, м", "кромка")
_SHEET_TITLES = ("hpl, листов",)


def parse_order_form_file(content: bytes, order_rows: pd.DataFrame) -> ParsedOrderFile:
    """Quantities from the file for the supplier's positions; `order_rows` lists all of them."""
    parsed = ParsedOrderFile(fingerprint=hashlib.sha256(content).hexdigest()[:12])
    try:
        sheet = load_workbook(BytesIO(content), data_only=True, read_only=True).worksheets[0]
        table = [list(row) for row in sheet.iter_rows(values_only=True)]
    except Exception:
        parsed.errors.append("Не удалось прочитать файл. Нужен Excel-файл заказа в формате .xlsx.")
        return parsed

    by_key: dict[str, dict[str, object]] = {}
    for record in order_rows.to_dict(orient="records"):
        if record["code"]:
            by_key[_normalize_code(record["code"])] = record
        else:
            by_key[_normalize_code(record["name"])] = record
        if record["old_code"] not in ("", "NEW"):
            by_key.setdefault(_normalize_code(record["old_code"]), record)

    # Each table of the form has its own header row, and files saved from the
    # earlier form layout are still accepted.
    columns: dict[str, int] = {}

    def cell(row: list[object], titles: tuple[str, ...]) -> object:
        for title in titles:
            position = columns.get(title)
            if position is not None and position < len(row) and row[position] is not None:
                return row[position]
        return None

    for number, row in enumerate(table, start=1):
        titles = {" ".join(str(value or "").casefold().split()): position for position, value in enumerate(row)}
        if "код" in titles and set(titles) & {*_BOARD_TITLES, *_EDGE_TITLES, *_SHEET_TITLES}:
            columns = titles
            continue
        if not columns:
            continue
        raw_code = cell(row, ("код",))
        raw_name = cell(row, ("название",))
        board = _file_quantity(cell(row, _BOARD_TITLES))
        edge = _file_quantity(cell(row, (*_EDGE_TITLES, *_SHEET_TITLES)))
        key = _normalize_code(raw_code) or _normalize_code(raw_name)
        label = str(raw_code if _normalize_code(raw_code) else raw_name or "").strip()
        if not key:
            continue  # The totals row and blank lines have no code or name.
        record = by_key.get(key)
        if record is None:
            # Section titles such as the HPL band are not goods and carry no numbers.
            if board or edge:
                parsed.unknown_codes.append(label)
            continue
        if board is None or edge is None:
            parsed.errors.append(f"Строка {number}, код {label}: количество должно быть числом не меньше нуля.")
            continue
        if record["section"] != SECTION_BOARDS:
            board, edge = 0, board + edge  # Sheets or pieces, in whichever column they were typed.
        elif "hpl, листов" in columns:
            board, edge = board + edge, 0  # A board typed under the HPL table has only this quantity column.
        previous = parsed.quantities.get(record["row_id"], (0, 0))
        parsed.quantities[record["row_id"]] = (previous[0] + board, previous[1] + edge)
    if not columns:
        parsed.errors.append("Это не форма заказа: не нашёл колонки с кодом и количеством.")
    return parsed


def apply_order_file(order_rows: pd.DataFrame, parsed: ParsedOrderFile) -> pd.DataFrame:
    """The form with the file's quantities: a position left blank in the file is not ordered."""
    result = order_rows.copy()
    result["quantity"] = [parsed.quantities.get(row_id, (0, 0))[0] for row_id in result["row_id"]]
    result["edge_or_sheets"] = [parsed.quantities.get(row_id, (0, 0))[1] for row_id in result["row_id"]]
    return result


def order_file_comment(filename: str, parsed: ParsedOrderFile) -> str:
    return f"Форма заказа WQ · файл {filename} · {parsed.fingerprint}"


def _store_total_value(content: bytes, total_cell: str, total_value: int) -> bytes:
    """Save the computed total next to its formula: Excel's Protected View shows only stored values."""
    source = BytesIO(content)
    target = BytesIO()
    pattern = re.compile(rf'(<c r="{total_cell}"[^>]*><f>[^<]*</f>)<v\s*/>|(<c r="{total_cell}"[^>]*><f>[^<]*</f>)<v></v>')
    with ZipFile(source) as archive, ZipFile(target, "w", ZIP_DEFLATED) as output:
        for item in archive.infolist():
            data = archive.read(item.filename)
            if item.filename.startswith("xl/worksheets/"):
                text = data.decode("utf-8")
                text = pattern.sub(lambda m: f"{m.group(1) or m.group(2)}<v>{total_value}</v>", text)
                data = text.encode("utf-8")
            output.writestr(item, data)
    return target.getvalue()


def build_wq_order_workbook(order_rows: pd.DataFrame, *, order_date: date | None = None) -> bytes:
    """The WQ order: only positions with a quantity, in separate tables for boards, HPL and other goods."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Заказ WQ"
    sheet.sheet_view.showGridLines = False

    navy = "003461"
    grid = Side(style="thin", color="B8C4D0")
    frame = Border(left=grid, right=grid, top=grid, bottom=grid)
    band = PatternFill("solid", fgColor="DCE6F1")
    header_fill = PatternFill("solid", fgColor=navy)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", indent=1)
    last_column = 6

    for column, width in {"A": 5, "B": 23, "C": 24, "D": 19, "E": 12, "F": 12}.items():
        sheet.column_dimensions[column].width = width

    def merged(row: int, start: int, end: int) -> None:
        sheet.merge_cells(start_row=row, start_column=start, end_row=row, end_column=end)

    def heading(row: int, text: str, font: Font, height: float) -> None:
        merged(row, 1, last_column)
        cell = sheet.cell(row=row, column=1, value=text)
        cell.font = font
        cell.alignment = Alignment(horizontal="left", vertical="center")
        sheet.row_dimensions[row].height = height

    def section(row: int, text: str) -> None:
        heading(row, text, Font(name="Calibri", size=12, bold=True, color=navy), 22)
        for column in range(1, last_column + 1):
            sheet.cell(row=row, column=column).fill = band

    def table_header(row: int, titles: dict[int, str]) -> None:
        for column in range(1, last_column + 1):
            cell = sheet.cell(row=row, column=column, value=titles.get(column))
            cell.fill = header_fill
            cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
            cell.alignment = center
            cell.border = frame
        sheet.row_dimensions[row].height = 32

    def body_cell(row: int, column: int, value: object, alignment: Alignment, *, bold: bool = False) -> None:
        cell = sheet.cell(row=row, column=column, value=value)
        cell.font = Font(name="Calibri", size=11, bold=bold)
        cell.alignment = alignment
        cell.border = frame

    records = [
        record
        for record in order_rows.to_dict(orient="records")
        if int(record["quantity"]) > 0 or int(record["edge_or_sheets"]) > 0
    ]
    board_records = [record for record in records if record["section"] == SECTION_BOARDS]
    hpl_records = [record for record in records if record["section"] == SECTION_HPL]
    other_records = [record for record in records if record["section"] == SECTION_OTHER]

    heading(1, "Заказ поставщику WQ", Font(name="Calibri", size=16, bold=True, color=navy), 26)
    heading(
        2,
        f"Дата заказа: {(order_date or date.today()):%d.%m.%Y}",
        Font(name="Calibri", size=11, color="5B6B7B"),
        18,
    )

    row = 4
    last_row = 2
    total_cell = ""
    if board_records:
        section(row, f"Плиты {WQ_BOARD_FORMAT}")
        row += 1
        table_header(row, {1: "№", 2: "Код", 3: "Название", 4: "Старый код", 5: "Плиты, шт", 6: "Кромка, м"})
        sheet.freeze_panes = f"A{row + 1}"
        row += 1
        first_board = row
        for position, record in enumerate(board_records, start=1):
            body_cell(row, 1, position, center)
            body_cell(row, 2, record["code"] or None, left, bold=True)
            body_cell(row, 3, record["name"], left)
            body_cell(row, 4, record["old_code"] or None, center)
            body_cell(row, 5, int(record["quantity"]) or None, center)
            body_cell(row, 6, int(record["edge_or_sheets"]) or None, center)
            row += 1
        last_board = row - 1

        merged(row, 1, 4)
        for column in range(1, last_column + 1):
            cell = sheet.cell(row=row, column=column)
            cell.fill = band
            cell.border = frame
        total_label = sheet.cell(row=row, column=1, value="Итого плит, шт")
        total_label.font = Font(name="Calibri", size=12, bold=True, color=navy)
        total_label.alignment = Alignment(horizontal="right", vertical="center", indent=1)
        total_cell = f"E{row}"
        sheet[total_cell] = f"=SUM(E{first_board}:E{last_board})"
        sheet[total_cell].font = Font(name="Calibri", size=13, bold=True, color=navy)
        sheet[total_cell].alignment = center
        sheet.row_dimensions[row].height = 22
        last_row = row
        row += 2

    def short_table(title: str, quantity_title: str, table_records: list[dict[str, object]]) -> None:
        nonlocal row, last_row
        section(row, title)
        row += 1
        table_header(row, {1: "№", 2: "Код", 3: "Название", 5: quantity_title})
        merged(row, 3, 4)
        merged(row, 5, 6)
        row += 1
        for position, record in enumerate(table_records, start=1):
            body_cell(row, 1, position, center)
            body_cell(row, 2, record["code"] or None, left, bold=True)
            body_cell(row, 3, record["name"], left)
            sheet.cell(row=row, column=4).border = frame
            merged(row, 3, 4)
            body_cell(row, 5, int(record["edge_or_sheets"]) or None, center)
            sheet.cell(row=row, column=6).border = frame
            merged(row, 5, 6)
            row += 1
        last_row = row - 1
        row += 1

    if hpl_records:
        short_table("HPL", "HPL, листов", hpl_records)
    if other_records:
        short_table("Другие позиции", "Кол-во", other_records)

    sheet.print_area = f"A1:F{max(last_row, 2)}"
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_options.horizontalCentered = True

    buffer = BytesIO()
    workbook.save(buffer)
    content = buffer.getvalue()
    return _store_total_value(content, total_cell, int(order_rows["quantity"].sum())) if total_cell else content
