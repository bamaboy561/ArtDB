from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

import pandas as pd

from sales_analytics import coerce_numeric, parse_dates


@dataclass(frozen=True)
class DataQualityIssue:
    severity: str
    title: str
    detail: str
    count: int = 0
    action: str = ""


@dataclass(frozen=True)
class DataQualityReport:
    status: str
    score: int
    raw_rows: int
    prepared_rows: int
    dropped_rows: int
    issues: tuple[DataQualityIssue, ...]
    problem_rows: pd.DataFrame
    catalog_metrics: dict[str, float]

    @property
    def can_save(self) -> bool:
        return self.status != "blocked"


def _mapped_series(frame: pd.DataFrame, mapping: dict[str, str | None], key: str) -> pd.Series | None:
    column = mapping.get(key)
    if not column or column not in frame.columns:
        return None
    return frame[column]


def _clean_product_series(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series("", index=index, dtype="object")
    return series.fillna("").astype(str).str.strip()


def _numeric_series(series: pd.Series | None, index: pd.Index) -> pd.Series:
    numeric = coerce_numeric(series)
    if numeric is None:
        return pd.Series(pd.NA, index=index, dtype="Float64")
    return numeric.astype("Float64")


def _add_problem_rows(
    rows: list[pd.DataFrame],
    frame: pd.DataFrame,
    mask: pd.Series,
    issue: str,
    *,
    limit: int = 20,
) -> None:
    if frame.empty or not bool(mask.any()):
        return

    columns = [
        column
        for column in ["date", "product", "category", "manager", "quantity", "revenue", "cost", "margin", "margin_pct"]
        if column in frame.columns
    ]
    problem_frame = frame.loc[mask, columns].head(limit).copy()
    problem_frame.insert(0, "issue", issue)
    rows.append(problem_frame)


def _catalog_keys(procurement_items: pd.DataFrame | None) -> set[str]:
    if procurement_items is None or procurement_items.empty or "product" not in procurement_items.columns:
        return set()
    return {
        str(product).strip().casefold()
        for product in procurement_items["product"].dropna().astype(str)
        if str(product).strip()
    }


def _score_from_issues(issues: Iterable[DataQualityIssue], raw_rows: int) -> int:
    score = 100
    safe_rows = max(raw_rows, 1)
    for issue in issues:
        ratio = min(max(issue.count, 1) / safe_rows, 1)
        if issue.severity == "blocker":
            score -= 45
        elif issue.severity == "warning":
            score -= max(6, int(24 * ratio))
        else:
            score -= max(1, int(8 * ratio))
    return max(0, min(score, 100))


def analyze_sales_quality(
    raw_frame: pd.DataFrame,
    prepared_frame: pd.DataFrame,
    mapping: dict[str, str | None],
    *,
    procurement_items: pd.DataFrame | None = None,
    expected_report_date: date | None = None,
    include_margin_checks: bool = True,
    today: date | None = None,
) -> DataQualityReport:
    today = today or date.today()
    issues: list[DataQualityIssue] = []
    problem_rows: list[pd.DataFrame] = []
    raw_rows = int(len(raw_frame))
    prepared_rows = int(len(prepared_frame))
    dropped_rows = max(raw_rows - prepared_rows, 0)

    if dropped_rows:
        dropped_share = dropped_rows / max(raw_rows, 1) * 100
        severity = "blocker" if dropped_share >= 50 else "warning"
        issues.append(
            DataQualityIssue(
                severity,
                "Часть строк не попала в анализ",
                f"После подготовки исключено {dropped_rows} строк ({dropped_share:.1f}%). Обычно причина: пустая дата, товар или выручка.",
                dropped_rows,
                "Проверьте исходный файл и сопоставление колонок перед сохранением.",
            )
        )

    raw_index = raw_frame.index
    raw_dates = parse_dates(_mapped_series(raw_frame, mapping, "date")) if mapping.get("date") else pd.Series(pd.NaT, index=raw_index)
    raw_products = _clean_product_series(_mapped_series(raw_frame, mapping, "product"), raw_index)
    raw_revenue = _numeric_series(_mapped_series(raw_frame, mapping, "revenue"), raw_index)

    missing_date_count = int(raw_dates.isna().sum())
    if missing_date_count:
        issues.append(
            DataQualityIssue(
                "warning",
                "Есть строки без даты",
                f"Не удалось прочитать дату в {missing_date_count} строках.",
                missing_date_count,
                "Проверьте формат даты или выберите другую колонку даты.",
            )
        )

    missing_product_count = int(raw_products.eq("").sum())
    if missing_product_count:
        issues.append(
            DataQualityIssue(
                "warning",
                "Есть строки без товара",
                f"Название товара пустое в {missing_product_count} строках.",
                missing_product_count,
                "Такие строки не дают точного ABC, закупок и остатков.",
            )
        )

    if mapping.get("revenue"):
        missing_revenue_count = int(raw_revenue.isna().sum())
        if missing_revenue_count:
            issues.append(
                DataQualityIssue(
                    "warning",
                    "Есть строки без выручки",
                    f"Выручка не распознана в {missing_revenue_count} строках.",
                    missing_revenue_count,
                    "Проверьте, что выбрана именно сумма продаж, а не текстовая колонка.",
                )
            )

    if prepared_frame.empty:
        issues.append(
            DataQualityIssue(
                "blocker",
                "Нет строк для анализа",
                "После подготовки не осталось данных, которые можно сохранить и анализировать.",
                raw_rows,
                "Проверьте файл и сопоставление обязательных колонок.",
            )
        )
        return DataQualityReport(
            "blocked",
            0,
            raw_rows,
            prepared_rows,
            dropped_rows,
            tuple(issues),
            pd.DataFrame(),
            {},
        )

    frame = prepared_frame.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    for numeric_column in ["quantity", "revenue", "cost", "margin", "margin_pct"]:
        if numeric_column in frame.columns:
            frame[numeric_column] = pd.to_numeric(frame[numeric_column], errors="coerce")

    future_mask = frame["date"].dt.date > (today + timedelta(days=1))
    future_count = int(future_mask.sum())
    if future_count:
        issues.append(
            DataQualityIssue(
                "blocker",
                "В файле есть будущие даты",
                f"Найдено строк с датой позже завтрашнего дня: {future_count}.",
                future_count,
                "Скорее всего, неверно распознана колонка даты или формат даты.",
            )
        )
        _add_problem_rows(problem_rows, frame, future_mask, "Будущая дата")

    old_mask = frame["date"].dt.date < (today - timedelta(days=365 * 5))
    old_count = int(old_mask.sum())
    if old_count:
        issues.append(
            DataQualityIssue(
                "warning",
                "Есть очень старые даты",
                f"Найдено строк старше 5 лет: {old_count}.",
                old_count,
                "Если это не исторический импорт, проверьте формат даты.",
            )
        )
        _add_problem_rows(problem_rows, frame, old_mask, "Очень старая дата")

    if expected_report_date is not None:
        unique_dates = sorted(frame["date"].dropna().dt.date.unique().tolist())
        if len(unique_dates) == 1 and unique_dates[0] != expected_report_date:
            issues.append(
                DataQualityIssue(
                    "warning",
                    "Дата файла отличается от даты сохранения",
                    f"В файле дата {unique_dates[0].strftime('%d.%m.%Y')}, а сохраняется как {expected_report_date.strftime('%d.%m.%Y')}.",
                    1,
                    "Если это не замена вручную, лучше сохранить с датой из файла.",
                )
            )
        elif len(unique_dates) > 31:
            issues.append(
                DataQualityIssue(
                    "info",
                    "Файл содержит длинный период",
                    f"В выгрузке найдено разных дат: {len(unique_dates)}.",
                    len(unique_dates),
                    "Для ежедневного архива обычно удобнее загружать один день, для разового анализа это нормально.",
                )
            )

    if "quantity" in frame.columns and "revenue" in frame.columns:
        return_mask = (frame["quantity"].fillna(0) < 0) | (frame["revenue"].fillna(0) < 0)
        return_count = int(return_mask.sum())
        if return_count:
            issues.append(
                DataQualityIssue(
                    "info",
                    "Найдены возвраты",
                    f"Строк с отрицательным количеством или выручкой: {return_count}.",
                    return_count,
                    "Они будут учтены в разделе возвратов и могут снижать маржу/выручку.",
                )
            )

        zero_revenue_mask = (frame["quantity"].fillna(0) > 0) & (frame["revenue"].fillna(0).abs() < 1e-9)
        zero_revenue_count = int(zero_revenue_mask.sum())
        if zero_revenue_count:
            issues.append(
                DataQualityIssue(
                    "warning",
                    "Есть продажи с нулевой выручкой",
                    f"Количество положительное, но выручка нулевая в {zero_revenue_count} строках.",
                    zero_revenue_count,
                    "Проверьте скидки, бонусные позиции или сопоставление выручки.",
                )
            )
            _add_problem_rows(problem_rows, frame, zero_revenue_mask, "Нулевая выручка")

        zero_qty_mask = frame["quantity"].fillna(0).abs().lt(1e-9) & frame["revenue"].fillna(0).abs().gt(1e-9)
        zero_qty_count = int(zero_qty_mask.sum())
        if zero_qty_count:
            issues.append(
                DataQualityIssue(
                    "warning",
                    "Есть выручка без количества",
                    f"Количество равно нулю, но выручка есть в {zero_qty_count} строках.",
                    zero_qty_count,
                    "Это искажает прогноз закупок, потому что спрос считается по количеству.",
                )
            )
            _add_problem_rows(problem_rows, frame, zero_qty_mask, "Выручка без количества")

    if include_margin_checks:
        missing_cost_count = int(frame["cost"].isna().sum()) if "cost" in frame.columns else prepared_rows
        if missing_cost_count:
            issues.append(
                DataQualityIssue(
                    "warning",
                    "Не вся себестоимость распознана",
                    f"Себестоимость отсутствует в {missing_cost_count} строках.",
                    missing_cost_count,
                    "Маржинальность по таким строкам будет неполной.",
                )
            )

        if "margin_pct" in frame.columns:
            extreme_margin_mask = frame["margin_pct"].notna() & (
                (frame["margin_pct"] < -50) | (frame["margin_pct"] > 100)
            )
            extreme_margin_count = int(extreme_margin_mask.sum())
            if extreme_margin_count:
                issues.append(
                    DataQualityIssue(
                        "warning",
                        "Есть аномальная маржинальность",
                        f"Маржа ниже -50% или выше 100% в {extreme_margin_count} строках.",
                        extreme_margin_count,
                        "Частая причина: перепутана себестоимость, выручка или количество.",
                    )
                )
                _add_problem_rows(problem_rows, frame, extreme_margin_mask, "Аномальная маржа")

    duplicate_columns = [column for column in ["date", "product", "manager", "quantity", "revenue"] if column in frame.columns]
    if duplicate_columns:
        duplicate_mask = frame.duplicated(subset=duplicate_columns, keep=False)
        duplicate_count = int(duplicate_mask.sum())
        if duplicate_count:
            issues.append(
                DataQualityIssue(
                    "info",
                    "Есть похожие дубли",
                    f"Повторяющихся строк по дате, товару, менеджеру, количеству и выручке: {duplicate_count}.",
                    duplicate_count,
                    "Если выгрузка уже агрегирована, это нормально; если это строки чеков, стоит проверить повторы.",
                )
            )
            _add_problem_rows(problem_rows, frame, duplicate_mask, "Похожий дубль")

    product_keys = {
        str(product).strip().casefold()
        for product in frame["product"].dropna().astype(str)
        if str(product).strip()
    }
    catalog_keys = _catalog_keys(procurement_items)
    catalog_metrics = {
        "products_in_file": float(len(product_keys)),
        "catalog_products": float(len(catalog_keys)),
        "matched_catalog_products": float(len(product_keys & catalog_keys)) if catalog_keys else 0.0,
        "new_products": float(len(product_keys - catalog_keys)) if catalog_keys else float(len(product_keys)),
    }
    if not catalog_keys:
        issues.append(
            DataQualityIssue(
                "info",
                "Справочник товаров пока пуст",
                "Программа сможет построить продажи, но закупки станут точнее после загрузки остатков и поставщиков.",
                len(product_keys),
                "Загрузите остатки в разделе закупок, чтобы связать товары с поставщиками.",
            )
        )
    elif catalog_metrics["new_products"] > 0:
        issues.append(
            DataQualityIssue(
                "warning",
                "Есть новые SKU вне справочника",
                f"В файле найдено новых товаров, которых нет в справочнике закупок: {int(catalog_metrics['new_products'])}.",
                int(catalog_metrics["new_products"]),
                "После сохранения проверьте поставщика, остаток и правила заказа для новых SKU.",
            )
        )

    status = "blocked" if any(issue.severity == "blocker" for issue in issues) else ("warning" if any(issue.severity == "warning" for issue in issues) else "ok")
    score = _score_from_issues(issues, raw_rows)
    problem_frame = pd.concat(problem_rows, ignore_index=True) if problem_rows else pd.DataFrame()

    return DataQualityReport(
        status=status,
        score=score,
        raw_rows=raw_rows,
        prepared_rows=prepared_rows,
        dropped_rows=dropped_rows,
        issues=tuple(issues),
        problem_rows=problem_frame,
        catalog_metrics=catalog_metrics,
    )


def build_catalog_health(procurement_items: pd.DataFrame, procurement_forecast: pd.DataFrame) -> dict[str, object]:
    catalog = procurement_items.copy() if procurement_items is not None else pd.DataFrame()
    forecast = procurement_forecast.copy() if procurement_forecast is not None else pd.DataFrame()

    catalog_products = _catalog_keys(catalog)
    forecast_products = _catalog_keys(forecast)
    missing_in_catalog = forecast_products - catalog_products if forecast_products else set()

    if forecast.empty:
        forecast_scope = catalog.copy()
    else:
        forecast_scope = forecast.copy()

    if forecast_scope.empty:
        return {
            "catalog_products": 0,
            "forecast_products": 0,
            "missing_in_catalog": 0,
            "missing_supplier": 0,
            "missing_lead_time": 0,
            "missing_order_rules": 0,
            "supplier_count": 0,
            "readiness_pct": 0.0,
        }

    supplier = forecast_scope.get("supplier", pd.Series("", index=forecast_scope.index)).fillna("").astype(str).str.strip()
    lead_time = pd.to_numeric(forecast_scope.get("lead_time_days", pd.Series(0, index=forecast_scope.index)), errors="coerce").fillna(0)
    min_order = pd.to_numeric(forecast_scope.get("min_order_qty", pd.Series(0, index=forecast_scope.index)), errors="coerce").fillna(0)
    multiple = pd.to_numeric(forecast_scope.get("order_multiple", pd.Series(0, index=forecast_scope.index)), errors="coerce").fillna(0)

    total_scope = max(int(len(forecast_scope)), 1)
    missing_supplier = int(supplier.eq("").sum())
    missing_lead_time = int(lead_time.le(0).sum())
    missing_order_rules = int((min_order.le(0) & multiple.le(0)).sum())

    coverage_score = 1 - (len(missing_in_catalog) / max(len(forecast_products), 1) if forecast_products else 0)
    supplier_score = 1 - missing_supplier / total_scope
    lead_time_score = 1 - missing_lead_time / total_scope
    order_rule_score = 1 - missing_order_rules / total_scope
    readiness_pct = max(0.0, min(100.0, (coverage_score * 0.35 + supplier_score * 0.3 + lead_time_score * 0.2 + order_rule_score * 0.15) * 100))

    return {
        "catalog_products": len(catalog_products),
        "forecast_products": len(forecast_products),
        "missing_in_catalog": len(missing_in_catalog),
        "missing_supplier": missing_supplier,
        "missing_lead_time": missing_lead_time,
        "missing_order_rules": missing_order_rules,
        "supplier_count": int(supplier[supplier.ne("")].nunique()),
        "readiness_pct": readiness_pct,
    }
