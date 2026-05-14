from __future__ import annotations
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from backend.models.database import get_db
from backend.models.operation import Operation, OperationStatus
from backend.api.operations import _build_financials_out, _get_expenses_data

router = APIRouter(prefix="/api/investor", tags=["investor"])


def _hold_months(op: Operation) -> int | None:
    if not op.dates:
        return None
    e = op.dates.escritura_date
    s = op.dates.sale_date
    if not e or not s:
        return None
    m = (s.year - e.year) * 12 + (s.month - e.month)
    return m if m > 0 else None


@router.get("/summary")
def get_investor_summary(db: Session = Depends(get_db)):
    all_ops  = db.query(Operation).all()
    vendidas = [op for op in all_ops if op.status == OperationStatus.vendido]
    prospectos = [
        op for op in all_ops
        if op.status in (OperationStatus.prospecto, OperationStatus.negociacion)
    ]

    # ── Track record ──────────────────────────────────────────────────────────
    capital_total = 0.0
    beneficio_total = 0.0
    roi_list: list[float] = []
    roi_anual_list: list[float] = []

    closed_deals = []
    investors_map: dict[str, dict] = {}

    for op in vendidas:
        total_exp, by_cat = _get_expenses_data(db, op.id)
        fin = _build_financials_out(op.financials, total_exp, by_cat)
        net = fin.get("net_profit")
        roi = fin.get("roi_pct")
        cap = fin.get("total_costes") or 0.0   # capital desplegado = total costes operación

        capital_total += cap
        if net is not None:
            beneficio_total += net
        if roi is not None:
            roi_list.append(roi)

        hold = _hold_months(op)
        roi_anual = round(roi / hold * 12, 2) if roi is not None and hold else None
        if roi_anual is not None:
            roi_anual_list.append(roi_anual)

        escritura = op.dates.escritura_date.isoformat() if op.dates and op.dates.escritura_date else None
        sale_date  = op.dates.sale_date.isoformat()     if op.dates and op.dates.sale_date      else None

        closed_deals.append({
            "name":           op.name,
            "address":        op.address or "",
            "neighborhood":   op.neighborhood or "",
            "escritura_date": escritura,
            "sale_date":      sale_date,
            "hold_months":    hold,
            "capital":        round(cap, 0),
            "net_profit":     round(net, 0) if net is not None else None,
            "roi_pct":        roi,
            "roi_anual_pct":  roi_anual,
        })

        # ── Investors (built in same loop to avoid double-querying) ───────────
        if net is None:
            continue
        for p in op.op_partners:
            name = p.name
            pct  = float(p.participation_pct)
            ganado = round(net * pct / 100, 2)
            # capital per investor: explicit contribution or proportional share of total_costes
            cc = (float(p.capital_contributed) if p.capital_contributed
                  else round(pct / 100 * cap, 2))
            if name not in investors_map:
                investors_map[name] = {
                    "name": name, "role": p.role or "Socio",
                    "ops": 0, "capital": 0.0, "beneficio": 0.0,
                }
            investors_map[name]["ops"]       += 1
            investors_map[name]["capital"]   += cc
            investors_map[name]["beneficio"] += ganado

    investors = []
    for inv in sorted(investors_map.values(), key=lambda x: x["beneficio"], reverse=True):
        cap = inv["capital"]
        ben = inv["beneficio"]
        roi = round(ben / cap * 100, 2) if cap > 0 else None
        investors.append({
            "name":      inv["name"],
            "role":      inv["role"],
            "ops":       inv["ops"],
            "capital":   round(cap, 0),
            "beneficio": round(ben, 0),
            "roi_pct":   roi,
        })

    # ── Oportunidades ─────────────────────────────────────────────────────────
    oportunidades = []
    for op in prospectos:
        pp = None
        if op.financials and op.financials.purchase_price:
            pp = float(op.financials.purchase_price)
        if pp is not None:
            oportunidades.append({
                "name":          op.name,
                "address":       op.address or "",
                "neighborhood":  op.neighborhood or op.district or "",
                "status":        op.status.value,
                "purchase_price": pp,
            })

    roi_medio        = round(sum(roi_list) / len(roi_list), 2)        if roi_list        else None
    roi_anual_medio  = round(sum(roi_anual_list) / len(roi_anual_list), 2) if roi_anual_list else None

    return {
        "track_record": {
            "ops_cerradas":      len(vendidas),
            "capital_total":     round(capital_total, 0),
            "beneficio_total":   round(beneficio_total, 0),
            "roi_medio":         roi_medio,
            "roi_anual_medio":   roi_anual_medio,
        },
        "closed_deals": closed_deals,
        "investors":    investors,
        "oportunidades": oportunidades,
    }
