from sqlalchemy.orm import Session
from backend.models.listing import Listing


PRICE_MIN = 50_000
PRICE_MAX = 2_000_000
SIZE_MIN = 15
SIZE_MAX = 1_000
PRICE_PER_M2_MAX = 20_000


class QAAgent:

    def run(self, db: Session) -> dict:
        listings = db.query(Listing).filter(Listing.score == None).all()
        print(f"[QA] Analizando {len(listings)} listings...")

        results = {"valid": 0, "flagged": 0, "issues": []}

        for listing in listings:
            issues = self._validate(listing)
            if issues:
                results["flagged"] += 1
                results["issues"].append({
                    "id": listing.id,
                    "title": listing.title,
                    "issues": issues
                })
                db.delete(listing)
            else:
                results["valid"] += 1

        db.commit()
        print(f"[QA] {results['valid']} válidos, {results['flagged']} eliminados.")
        return results

    def _validate(self, listing: Listing) -> list[str]:
        issues = []

        if listing.price is None:
            issues.append("sin precio")
        elif listing.price < PRICE_MIN:
            issues.append(f"precio muy bajo: {listing.price}€")
        elif listing.price > PRICE_MAX:
            issues.append(f"precio muy alto: {listing.price}€")

        if listing.size_m2 is not None:
            if listing.size_m2 < SIZE_MIN:
                issues.append(f"tamaño muy pequeño: {listing.size_m2}m²")
            elif listing.size_m2 > SIZE_MAX:
                issues.append(f"tamaño muy grande: {listing.size_m2}m²")

        if listing.price and listing.size_m2:
            ppm2 = listing.price / listing.size_m2
            if ppm2 > PRICE_PER_M2_MAX:
                issues.append(f"precio/m² anómalo: {ppm2:.0f}€/m²")

        if self._is_rental(listing.title):
            issues.append("es alquiler, no venta")

        return issues

    def _is_rental(self, title: str) -> bool:
        title_lower = title.lower()
        rental_keywords = ["alquiler", "alquilo", "arriendo", "renta mensual", "mes"]
        return any(kw in title_lower for kw in rental_keywords)
