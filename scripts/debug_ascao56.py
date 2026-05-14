"""
Debug script: imprime todos los valores del P&L de Ascao 56
"""
from dotenv import load_dotenv
load_dotenv()

from backend.models.database import SessionLocal
from backend.models.operation import Operation, OperationExpense, ExpenseCategory
from backend.api.operations import _build_financials_out, _get_expenses_data
from sqlalchemy import func

db = SessionLocal()
op = db.query(Operation).filter_by(name="Ascao 56").first()
if not op:
    print("ERROR: Operación 'Ascao 56' no encontrada")
    db.close()
    raise SystemExit(1)

# ── Obtener P&L completo ───────────────────────────────────────────────────────
total_exp, by_cat = _get_expenses_data(db, op.id)
fin = _build_financials_out(op.financials, total_exp, by_cat)
ebc = fin.get("expenses_by_category", {})

# ── Extraer valores intermedios ────────────────────────────────────────────────
pp         = fin.get("purchase_price") or 0
buy_costs  = ebc.get("compra", 0)
buy_agency = ebc.get("buy_agency", 0)
total_compra = fin.get("total_purchase_cost") or 0

reforma       = ebc.get("reforma", 0)
reforma_extra = ebc.get("reforma_extra", 0)
total_obra    = reforma + reforma_extra

tramites    = ebc.get("tramites", 0)
gastos_corr = ebc.get("gastos_corrientes", 0)
otros       = ebc.get("otros", 0)
total_otros = gastos_corr + otros

# comunidad y suministros por separado (gastos_corrientes los suma juntos)
rows_cat = (
    db.query(OperationExpense.category, func.sum(OperationExpense.amount))
    .filter_by(operation_id=op.id)
    .group_by(OperationExpense.category)
    .all()
)
cat_dict = {row[0].value: float(row[1]) for row in rows_cat}
comunidad   = cat_dict.get("comunidad", 0)
suministros = cat_dict.get("suministros", 0)

sell_agency  = ebc.get("sell_agency", 0)
loan_cost    = fin.get("financing_cost") or 0
total_costes = fin.get("total_costes") or 0

asp           = fin.get("actual_sale_price") or 0
beneficio_bruto = fin.get("gross_profit")
plusvalia     = fin.get("sale_tax_estimate") or 0
net_profit    = fin.get("net_profit")
irpf_is       = ((beneficio_bruto - net_profit)
                 if beneficio_bruto is not None and net_profit is not None
                 else None)
roi_pct       = fin.get("roi_pct")

# hold meses y ROI anualizado desde fechas de la operación
hold_months   = None
roi_anualizado = None
if op.dates and op.dates.escritura_date and op.dates.sale_date:
    e, s = op.dates.escritura_date, op.dates.sale_date
    hold_months = (s.year - e.year) * 12 + (s.month - e.month)
    if hold_months > 0 and roi_pct is not None:
        roi_anualizado = round(roi_pct / hold_months * 12, 1)

# ── Imprimir P&L ──────────────────────────────────────────────────────────────
def fmt(val, pct=False):
    if val is None:
        return "NULL"
    if pct:
        return f"{val:>8.1f}%"
    return f"{val:>12,.0f}€"

fields = [
    ("purchase_price",    pp,             "Precio compra"),
    ("buy_costs",         buy_costs,      "Gastos compra (ITP+notaría+tasas+registro)"),
    ("buy_agency",        buy_agency,     "Comisión inmobiliaria compra"),
    ("total_compra",      total_compra,   "TOTAL COSTES COMPRA"),
    ("reforma",           reforma,        "Reforma y construcción"),
    ("reforma_extra",     reforma_extra,  "Materiales y acabados"),
    ("total_obra",        total_obra,     "TOTAL OBRA"),
    ("honorarios",        tramites,       "Honorarios (arquitecto, certif, notario venta...)"),
    ("total_tramites",    tramites,       "TOTAL TRÁMITES"),
    ("comunidad",         comunidad,      "Comunidad"),
    ("suministros",       suministros,    "Suministros"),
    ("otros",             otros,          "Otros"),
    ("total_otros",       total_otros,    "TOTAL OTROS"),
    ("sell_agency",       sell_agency,    "Comisión venta"),
    ("loan_cost",         loan_cost,      "Coste financiero"),
    ("total_costes",      total_costes,   "████ TOTAL COSTES INVERTIDOS"),
    ("actual_sale_price", asp,            "Precio venta"),
    ("beneficio_bruto",   beneficio_bruto,"BENEFICIO BRUTO"),
    ("plusvalia",         plusvalia,      "Plusvalía municipal"),
    ("irpf_is",           irpf_is,        "IRPF calculado"),
    ("net_profit",        net_profit,     "████ BENEFICIO NETO"),
    ("roi_pct",           roi_pct,        "ROI %"),
    ("roi_anualizado",    roi_anualizado, "ROI anualizado %"),
    ("hold_months",       hold_months,    "Hold (meses)"),
]

print("\n" + "="*62)
print("P&L ASCAO 56 — VALORES INTERMEDIOS")
print("="*62)
for key, val, label in fields:
    is_pct = key in ("roi_pct", "roi_anualizado")
    is_months = key == "hold_months"
    if val is None:
        display = f"{'NULL':>12}"
    elif is_pct:
        display = f"{val:>11.1f}%"
    elif is_months:
        display = f"{val:>9} meses"
    else:
        display = f"{val:>12,.0f}€"
    print(f"  {label:<47} {display}")
print("="*62)

# ── Gastos por categoría ───────────────────────────────────────────────────────
print("\nGASTOS POR CATEGORÍA:")
total_gastos = 0
for cat_str, amt in sorted(cat_dict.items(), key=lambda x: -x[1]):
    print(f"  {cat_str:<22} {amt:>12,.0f}€")
    total_gastos += amt

print(f"  {'TOTAL GASTOS':<22} {total_gastos:>12,.0f}€")
print(f"  {'+ PRECIO COMPRA':<22} {pp:>12,.0f}€")
print(f"  {'= TOTAL INVERTIDO':<22} {total_gastos + pp:>12,.0f}€")
print(f"\n  (total_costes de _build) {total_costes:>12,.0f}€")
print()

db.close()
