"""
Seed script: carga datos reales del proyecto Ascao 56.
Borra la operación existente primero.
"""
from dotenv import load_dotenv
load_dotenv()

import uuid
from datetime import date, datetime
from decimal import Decimal
from backend.models.database import SessionLocal
from backend.models.operation import (
    Operation, OperationStatus, OperationFinancials,
    OperationDates, OperationExpense, OperationPartner,
    ExpenseCategory, PaidBy
)

db = SessionLocal()

# 1. Borrar operación existente (la única que hay)
existing = db.query(Operation).first()
if existing:
    op_id = existing.id
    db.query(OperationExpense).filter_by(operation_id=op_id).delete()
    db.query(OperationPartner).filter_by(operation_id=op_id).delete()
    db.query(OperationFinancials).filter_by(operation_id=op_id).delete()
    db.query(OperationDates).filter_by(operation_id=op_id).delete()
    db.delete(existing)
    db.commit()
    print("Operación existente borrada.")

# 2. Crear operación
op = Operation(
    name="Ascao 56",
    status=OperationStatus.vendido,
    address="C. de Ascao, 56 Piso 1 Derecha",
    neighborhood="Ciudad Lineal",
    district="Ciudad Lineal",
    lat=40.4378,
    lon=-3.6517,
)
db.add(op)
db.flush()

# 3. Fechas
db.add(OperationDates(
    operation_id=op.id,
    offer_date=date(2025, 8, 1),
    arras_date=date(2025, 8, 8),
    escritura_date=date(2025, 10, 20),
    renovation_start=date(2025, 11, 1),
    renovation_end=date(2026, 3, 1),
    listing_date=date(2026, 3, 10),
    sale_date=date(2026, 5, 20),
))

# 4. Financiero — solo precio escritura, precio venta, reforma estimada, financiación e impuestos
# ITP, notaría, comisiones se calculan desde OperationExpense
db.add(OperationFinancials(
    operation_id=op.id,
    purchase_price=Decimal("320000"),   # precio total escritura (incluye arras)
    renovation_budget=Decimal("70000"),
    target_sale_price=Decimal("490000"),
    actual_sale_price=Decimal("505000"),
    sale_tax_estimate=Decimal("2000"),  # plusvalía municipal (campo manual)
    financing_own_capital=Decimal("320000"),
    financing_borrowed=Decimal("0"),
    financing_cost=Decimal("0"),
    tax_regime="persona_fisica",
))

# 5. Socios — capital se calcula automáticamente como pct/100 * total_costes
for name, pct in [("Francisco", 50), ("Germán", 50)]:
    db.add(OperationPartner(
        operation_id=op.id,
        name=name,
        role="socio",
        participation_pct=Decimal(str(pct)),
    ))

# 6. Gastos
gastos = [
    # COMPRA — solo gastos auxiliares (el precio del piso está en purchase_price)
    (date(2025,10,20),"Notario",                    ExpenseCategory.compra,        979,    PaidBy.sl),
    (date(2025,10,20),"Tasas Ayuntamiento",          ExpenseCategory.compra,        1010,   PaidBy.sl),
    (date(2025,10,20),"ITP",                        ExpenseCategory.compra,        19200,  PaidBy.sl),
    (date(2025,10,20),"Registro Propiedad",          ExpenseCategory.compra,        521,    PaidBy.sl),
    (date(2025,10,20),"Mario ITP (gestoría)",        ExpenseCategory.compra,        605,    PaidBy.sl),
    # AGENCIA
    (date(2025,8,8),  "Inmobiliaria compra",         ExpenseCategory.agencia,       14144,  PaidBy.sl),
    (date(2026,5,20), "Tecnocasa comisión venta",    ExpenseCategory.agencia,       2500,   PaidBy.sl),
    # HONORARIOS
    (date(2025,11,1), "1er Pago Arquitecto",         ExpenseCategory.honorarios,    600,    PaidBy.sl),
    # REFORMA
    (date(2025,11,1), "Pago 1 Obra",                ExpenseCategory.reforma,       20430,  PaidBy.sl),
    (date(2025,11,1), "Pago Desmontador x2",         ExpenseCategory.reforma,       200,    PaidBy.sl),
    (date(2025,11,15),"Pago Inicial 5%",             ExpenseCategory.reforma,       1236,   PaidBy.sl),
    (date(2025,12,15),"Albañilería",                 ExpenseCategory.reforma,       11126,  PaidBy.sl),
    (date(2026,1,15), "Pago 45% Solados",            ExpenseCategory.reforma,       11126,  PaidBy.sl),
    (date(2026,2,15), "Obra 5% Final",               ExpenseCategory.reforma,       2449,   PaidBy.sl),
    # MATERIALES
    (date(2025,11,15),"Seña Cocina",                 ExpenseCategory.reforma_extra, 1900,   PaidBy.sl),
    (date(2025,12,1), "Tarima",                      ExpenseCategory.reforma_extra, 1947,   PaidBy.sl),
    (date(2025,12,1), "Grifería",                    ExpenseCategory.reforma_extra, 1527,   PaidBy.sl),
    (date(2025,12,15),"Solados y Cerámica",          ExpenseCategory.reforma_extra, 1205,   PaidBy.sl),
    (date(2025,12,15),"Iluminación",                 ExpenseCategory.reforma_extra, 716,    PaidBy.sl),
    (date(2025,12,1), "Ventanas 1",                  ExpenseCategory.reforma_extra, 6211,   PaidBy.sl),
    (date(2026,1,1),  "Ventanas 2",                  ExpenseCategory.reforma_extra, 6211,   PaidBy.sl),
    (date(2026,1,15), "Baño Azulejos Adicionales",   ExpenseCategory.reforma_extra, 318,    PaidBy.sl),
    (date(2026,1,15), "Mamparas",                    ExpenseCategory.reforma_extra, 1168,   PaidBy.sl),
    (date(2026,1,15), "Muebles Baño",                ExpenseCategory.reforma_extra, 1539,   PaidBy.sl),
    (date(2026,2,1),  "Pago Medio Cocina",           ExpenseCategory.reforma_extra, 3300,   PaidBy.sl),
    (date(2026,2,15), "Pago Final Cocina",           ExpenseCategory.reforma_extra, 2294,   PaidBy.sl),
    (date(2026,2,15), "Luces Terraza",               ExpenseCategory.reforma_extra, 140,    PaidBy.sl),
    (date(2026,2,1),  "Adicionales obra",            ExpenseCategory.reforma_extra, 1150,   PaidBy.sl),
    (date(2026,2,1),  "Cuenta materiales varios",    ExpenseCategory.reforma_extra, 5461,   PaidBy.sl),
    # SUMINISTROS
    (date(2025,11,30),"Recibo Gas Natural",          ExpenseCategory.suministros,   30,     PaidBy.sl),
    (date(2025,12,31),"Recibo Gas Natural",          ExpenseCategory.suministros,   77,     PaidBy.sl),
    (date(2026,1,31), "Recibo Gas Natural",          ExpenseCategory.suministros,   59,     PaidBy.sl),
    (date(2026,2,28), "Recibo Gas Natural",          ExpenseCategory.suministros,   88,     PaidBy.sl),
    (date(2026,3,31), "Recibo Gas Natural",          ExpenseCategory.suministros,   32,     PaidBy.sl),
    (date(2026,4,30), "Recibo Gas Natural",          ExpenseCategory.suministros,   5,      PaidBy.sl),
    # COMUNIDAD
    (date(2026,2,1),  "Comunidad",                   ExpenseCategory.comunidad,     430,    PaidBy.sl),
    (date(2026,4,30), "Comunidad Final",              ExpenseCategory.comunidad,     1370,   PaidBy.sl),
    # GASTOS PENDIENTES DE VENTA
    (date(2026,5,1),  "Derramas (7 meses)",          ExpenseCategory.comunidad,     1120,   PaidBy.sl),
    (date(2026,5,20), "Notaría venta",               ExpenseCategory.honorarios,    800,    PaidBy.sl),
    (date(2026,5,20), "Comunidad pendiente",         ExpenseCategory.comunidad,     90,     PaidBy.sl),
    # HONORARIOS
    (date(2026,5,20), "Certificado Eficiencia Energética", ExpenseCategory.honorarios, 73,  PaidBy.sl),
    (date(2026,5,20), "Tincho vendedor",             ExpenseCategory.honorarios,    1250,   PaidBy.sl),
    (date(2026,5,20), "Abril secretaria",            ExpenseCategory.honorarios,    100,    PaidBy.sl),
    # OTROS
    (date(2026,5,20), "A mano Ger (deuda saldada - anticipo Martín)", ExpenseCategory.otros, 1350, PaidBy.sl),
]

for fecha, desc, cat, amount, paid in gastos:
    db.add(OperationExpense(
        operation_id=op.id,
        date=fecha,
        description=desc,
        category=cat,
        amount=Decimal(str(amount)),
        paid_by=paid,
    ))

db.commit()
print(f"✓ Ascao 56 cargado con {len(gastos)} gastos.")
db.close()
