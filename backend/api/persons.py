from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import Operation, OperationStatus
from backend.auth.dependencies import get_current_user
from backend.models.operation import User
from backend.api.operations import _build_financials_out, _get_expenses_data

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("/summary")
def get_summary(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    all_ops  = db.query(Operation).all()
    vendidas = [op for op in all_ops if op.status == OperationStatus.vendido]

    # capital_total = sum of total_costes across ALL operations
    capital_total = 0.0
    for op in all_ops:
        total_exp, by_cat = _get_expenses_data(db, op.id)
        fin_data = _build_financials_out(op.financials, total_exp, by_cat)
        capital_total += fin_data.get("total_costes") or 0.0

    partners_map: dict[str, dict] = {}
    beneficio_total = 0.0
    ops_cerradas = 0

    for op in vendidas:
        total_exp, by_cat = _get_expenses_data(db, op.id)
        fin_data = _build_financials_out(op.financials, total_exp, by_cat)
        net_profit = fin_data.get("net_profit")
        if net_profit is None:
            continue
        ops_cerradas += 1
        total_costes = fin_data.get("total_costes") or 0.0
        beneficio_local = 0.0

        for p in op.op_partners:
            name = p.name
            pct  = float(p.participation_pct)
            ganado = round(net_profit * pct / 100, 2)
            # capital_aportado: always pct/100 * total_costes (capital_contributed is informational)
            cc  = round(pct / 100 * total_costes, 2)
            la  = float(p.loan_amount)           if p.loan_amount          else 0
            lir = float(p.loan_interest_rate)    if p.loan_interest_rate   else 0
            lm  = p.loan_months or 0
            loan_cost = round(la * (lir / 100) * (lm / 12), 2) if la and lir and lm else 0
            beneficio_local += ganado

            if name not in partners_map:
                partners_map[name] = {
                    "name": name, "role": p.role or "",
                    "ops": 0, "capital_aportado": 0.0,
                    "total_ganado": 0.0, "loan_total": 0.0,
                }
            partners_map[name]["ops"]             += 1
            partners_map[name]["capital_aportado"] += cc
            partners_map[name]["total_ganado"]     += ganado
            partners_map[name]["loan_total"]       += round(la + loan_cost, 2)

        beneficio_total += beneficio_local

    partners_list = sorted(partners_map.values(), key=lambda x: x["total_ganado"], reverse=True)
    for p in partners_list:
        p["total_ganado"]     = round(p["total_ganado"], 2)
        p["capital_aportado"] = round(p["capital_aportado"], 2)

    return {
        "kpis": {
            "capital_total":   round(capital_total, 2),
            "beneficio_total": round(beneficio_total, 2),
            "ops_cerradas":    ops_cerradas,
        },
        "partners": partners_list,
    }
