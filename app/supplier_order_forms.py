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
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


SECTION_BOARDS = "boards"
SECTION_HPL = "hpl"
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


WQ_BOARD_FORMAT = "2800*1220*18мм"
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
    "found",
    "product",
    "edge_product",
    "supplier",
    "low_priority_skipped",
]

_EDGE_SIZE = re.compile(r"(?<![0-9])1\s*[*xх×]\s*22(?![0-9])")
_BOARD_SIZE_PARTS = ("18", "1220", "2800")
_NUMBER = re.compile(r"[0-9]+")


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


def _row_mask(row: OrderFormRow, texts: pd.Series, kinds: pd.Series, kind: str) -> pd.Series:
    """Rows of the forecast that are this form row: by article first, by name otherwise."""
    candidates = texts[kinds.eq(kind)]
    terms = [row.code, *_old_code_terms(row.old_code)]
    by_code = candidates[candidates.map(lambda text: any(_contains_term(text, term) for term in terms)).astype(bool)]
    if by_code.empty and row.match_by_name:
        by_code = candidates[candidates.map(lambda text: _contains_term(text, row.name)).astype(bool)]
    return texts.index.isin(by_code.index)


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


def _proposed_rows(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty or "priority" not in frame.columns:
        return frame
    return frame[frame["priority"].fillna("").astype(str).str.strip().str.casefold().ne(EXCLUDED_ORDER_PRIORITY)]


def build_order_form_rows(
    procurement_forecast: pd.DataFrame,
    rows: tuple[OrderFormRow, ...] = WQ_ORDER_ROWS,
) -> pd.DataFrame:
    """One line per form row: boards, edge metres or HPL sheets, as the forecast recommends."""
    if procurement_forecast.empty or "product" not in procurement_forecast.columns:
        forecast = pd.DataFrame(columns=["product", "recommended_order_qty"])
    else:
        forecast = procurement_forecast.reset_index(drop=True)
    texts = forecast["product"].map(_normalize)
    kinds = texts.map(_product_kind)

    def first_value(frame: pd.DataFrame, column: str, default: object) -> object:
        if column not in frame.columns or frame.empty:
            return default
        return frame[column].iloc[0]

    def total(frame: pd.DataFrame, column: str) -> float:
        if column not in frame.columns:
            return 0.0
        return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())

    def first_product(frame: pd.DataFrame) -> str:
        return str(frame["product"].iloc[0]) if not frame.empty else ""

    records = []
    for row in rows:
        main_kind = "hpl" if row.section == SECTION_HPL else "board"
        main = forecast[_row_mask(row, texts, kinds, main_kind)]
        main_quantity = _whole_quantity(_proposed_rows(main)["recommended_order_qty"]) if not main.empty else 0
        skipped = _whole_quantity(main["recommended_order_qty"]) > main_quantity if not main.empty else False
        edge_found = False
        edge = forecast.iloc[0:0]
        if row.section == SECTION_HPL:
            quantity, edge_or_sheets = 0, main_quantity
        else:
            edge = forecast[_row_mask(row, texts, kinds, "edge")]
            edge_found = not edge.empty
            quantity = main_quantity
            edge_or_sheets = _whole_quantity(_proposed_rows(edge)["recommended_order_qty"]) if edge_found else 0
            if edge_found and _whole_quantity(edge["recommended_order_qty"]) > edge_or_sheets:
                skipped = True
        records.append(
            {
                "code": row.code,
                "quantity": quantity,
                "old_code": row.old_code,
                "edge_or_sheets": edge_or_sheets,
                "name": row.name,
                "section": row.section,
                "abc_class": first_value(main, "abc_class", ""),
                "xyz_class": first_value(main, "xyz_class", ""),
                "priority": first_value(main, "priority", ""),
                "stock_status": first_value(main, "stock_status", ""),
                "available_stock_qty": total(main, "available_stock_qty"),
                "forecast_qty": total(main, "forecast_qty"),
                "found": (not main.empty) or edge_found,
                "product": first_product(main),
                "edge_product": first_product(edge),
                "supplier": first_value(main, "supplier", ""),
                "low_priority_skipped": bool(skipped),
            }
        )
    return pd.DataFrame(records, columns=ORDER_FORM_COLUMNS)


def order_form_from_items(items: pd.DataFrame) -> pd.DataFrame:
    """The form for a saved order: its own quantities, not the current recommendation."""
    return build_order_form_rows(items.rename(columns={"quantity": "recommended_order_qty"}))


def order_items_from_form(order_rows: pd.DataFrame) -> pd.DataFrame:
    """Order lines for the form as it stands, so the ordered goods count as in transit."""
    quantities: dict[str, float] = {}
    for record in order_rows.to_dict(orient="records"):
        # A position without sales has no site product yet: name it so that it
        # is not lost and a saved order can rebuild the same form.
        label = f"{record['code']} {record['name']}"
        if record["section"] == SECTION_HPL:
            lines = ((record["product"] or f"HPL {label}", record["edge_or_sheets"]),)
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


def parse_order_form_file(content: bytes, rows: tuple[OrderFormRow, ...] = WQ_ORDER_ROWS) -> ParsedOrderFile:
    parsed = ParsedOrderFile(fingerprint=hashlib.sha256(content).hexdigest()[:12])
    try:
        sheet = load_workbook(BytesIO(content), data_only=True, read_only=True).worksheets[0]
        table = [list(row) for row in sheet.iter_rows(values_only=True)]
    except Exception:
        parsed.errors.append("Не удалось прочитать файл. Нужен Excel-файл заказа в формате .xlsx.")
        return parsed

    by_code: dict[str, OrderFormRow] = {}
    for form_row in rows:
        by_code[_normalize_code(form_row.code)] = form_row
        for term in [form_row.old_code] if form_row.old_code not in ("", "NEW") else []:
            by_code.setdefault(_normalize_code(term), form_row)

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
        board = _file_quantity(cell(row, _BOARD_TITLES))
        edge = _file_quantity(cell(row, (*_EDGE_TITLES, *_SHEET_TITLES)))
        code = _normalize_code(raw_code)
        if not code:
            continue  # The totals row and blank lines have no code.
        form_row = by_code.get(code)
        if form_row is None:
            # Section titles such as the HPL band are not goods and carry no numbers.
            if board or edge:
                parsed.unknown_codes.append(str(raw_code).strip())
            continue
        if board is None or edge is None:
            parsed.errors.append(f"Строка {number}, код {str(raw_code).strip()}: количество должно быть числом не меньше нуля.")
            continue
        if form_row.section == SECTION_HPL:
            board, edge = 0, board + edge  # Sheets, in whichever column they were typed.
        previous = parsed.quantities.get(form_row.code, (0, 0))
        parsed.quantities[form_row.code] = (previous[0] + board, previous[1] + edge)
    if not columns:
        parsed.errors.append("Это не форма заказа: не нашёл колонки с кодом и количеством.")
    return parsed


def apply_order_file(order_rows: pd.DataFrame, parsed: ParsedOrderFile) -> pd.DataFrame:
    """The form with the file's quantities: a position left blank in the file is not ordered."""
    result = order_rows.copy()
    result["quantity"] = [parsed.quantities.get(code, (0, 0))[0] for code in result["code"]]
    result["edge_or_sheets"] = [parsed.quantities.get(code, (0, 0))[1] for code in result["code"]]
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
    """The WQ order: a heading, the boards table with its total, then the HPL table."""
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

    records = order_rows.to_dict(orient="records")
    board_records = [record for record in records if record["section"] == SECTION_BOARDS]
    hpl_records = [record for record in records if record["section"] == SECTION_HPL]

    heading(1, "Заказ поставщику WQ", Font(name="Calibri", size=16, bold=True, color=navy), 26)
    heading(
        2,
        f"Дата заказа: {(order_date or date.today()):%d.%m.%Y}",
        Font(name="Calibri", size=11, color="5B6B7B"),
        18,
    )

    section(4, f"Плиты {WQ_BOARD_FORMAT}")
    board_header = 5
    table_header(
        board_header,
        {1: "№", 2: "Код", 3: "Название", 4: "Старый код", 5: "Плиты, шт", 6: "Кромка, м"},
    )
    row = board_header + 1
    first_board = row
    for position, record in enumerate(board_records, start=1):
        body_cell(row, 1, position, center)
        body_cell(row, 2, record["code"], left, bold=True)
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

    ordered_style = {"fill": PatternFill(bgColor="E2EFDA"), "font": Font(bold=True)}
    sheet.conditional_formatting.add(
        f"A{first_board}:F{last_board}",
        FormulaRule(formula=[f"OR(N($E{first_board})>0,N($F{first_board})>0)"], **ordered_style),
    )

    if hpl_records:
        row += 2
        section(row, "HPL")
        row += 1
        table_header(row, {1: "№", 2: "Код", 3: "Название", 5: "HPL, листов"})
        merged(row, 3, 4)
        merged(row, 5, 6)
        row += 1
        first_hpl = row
        for position, record in enumerate(hpl_records, start=1):
            body_cell(row, 1, position, center)
            body_cell(row, 2, record["code"], left, bold=True)
            body_cell(row, 3, record["name"], left)
            sheet.cell(row=row, column=4).border = frame
            merged(row, 3, 4)
            body_cell(row, 5, int(record["edge_or_sheets"]) or None, center)
            sheet.cell(row=row, column=6).border = frame
            merged(row, 5, 6)
            row += 1
        last_row = row - 1
        sheet.conditional_formatting.add(
            f"A{first_hpl}:F{last_row}",
            FormulaRule(formula=[f"N($E{first_hpl})>0"], **ordered_style),
        )

    sheet.freeze_panes = f"A{first_board}"
    sheet.print_area = f"A1:F{last_row}"
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 1
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_options.horizontalCentered = True

    buffer = BytesIO()
    workbook.save(buffer)
    return _store_total_value(buffer.getvalue(), total_cell, int(order_rows["quantity"].sum()))
