"""
enrich_location — añade lat/lon a listings sin coordenadas.
Usa un diccionario estático de barrios de Madrid. Sin API, sin rate limits.
"""
from backend.models.database import SessionLocal
from backend.models.listing import Listing

# Diccionario de barrios de Madrid → (lat, lon)
# Cubre los 128 barrios oficiales + variantes comunes de scrapers
BARRIOS_MADRID: dict[str, tuple[float, float]] = {
    # Centro
    "Palacio": (40.4143, -3.7138),
    "Embajadores": (40.4063, -3.7072),
    "Lavapiés": (40.4063, -3.7072),
    "Embajadores - Lavapiés": (40.4063, -3.7072),
    "Cortes": (40.4138, -3.6985),
    "Justicia": (40.4225, -3.6970),
    "Justicia - Chueca": (40.4225, -3.6970),
    "Universidad": (40.4230, -3.7072),
    "Universidad - Malasaña": (40.4260, -3.7030),
    "Malasaña": (40.4260, -3.7030),
    "Sol": (40.4168, -3.7038),
    "Centro - Sol": (40.4168, -3.7038),
    "Centro - Malasaña": (40.4260, -3.7030),
    "Chueca": (40.4225, -3.6970),

    # Arganzuela
    "Imperial": (40.4063, -3.7200),
    "Acacias": (40.3998, -3.7063),
    "Chopera": (40.3942, -3.6942),
    "Legazpi": (40.3895, -3.6942),
    "Delicias": (40.3942, -3.6870),
    "Palos de Moguer": (40.4007, -3.6942),
    "Atocha": (40.4063, -3.6898),

    # Retiro
    "Pacífico": (40.4007, -3.6820),
    "Adelfas": (40.3951, -3.6763),
    "Estrella": (40.4007, -3.6763),
    "Ibiza": (40.4063, -3.6763),
    "Jerónimos": (40.4138, -3.6878),
    "Niño Jesús": (40.4007, -3.6820),

    # Salamanca
    "Recoletos": (40.4225, -3.6878),
    "Goya": (40.4260, -3.6763),
    "Salamanca - Goya": (40.4260, -3.6763),
    "Fuente del Berro": (40.4260, -3.6707),
    "Guindalera": (40.4318, -3.6707),
    "Salamanca - La Guindalera": (40.4318, -3.6707),
    "Lista": (40.4295, -3.6820),
    "Castellana": (40.4318, -3.6878),

    # Chamartín
    "El Viso": (40.4490, -3.6878),
    "Prosperidad": (40.4490, -3.6763),
    "Ciudad Jardín": (40.4490, -3.6650),
    "Hispanoamérica": (40.4547, -3.6820),
    "Nueva España": (40.4605, -3.6878),
    "Pinar del Rey": (40.4605, -3.6763),

    # Tetuán
    "Bellas Vistas": (40.4547, -3.7038),
    "Tetuán - Bellas Vistas": (40.4547, -3.7038),
    "Cuatro Caminos": (40.4490, -3.7038),
    "Castillejos": (40.4547, -3.6970),
    "Almenara": (40.4605, -3.7038),
    "Valdeacederas": (40.4605, -3.6970),
    "Berruguete": (40.4547, -3.7105),
    "Tetuán": (40.4570, -3.7000),

    # Chamberí
    "Gaztambide": (40.4378, -3.7138),
    "Arapiles": (40.4378, -3.7072),
    "Chamberí - Arapiles": (40.4378, -3.7072),
    "Trafalgar": (40.4318, -3.7005),
    "Almagro": (40.4378, -3.6942),
    "Ríos Rosas": (40.4435, -3.7005),
    "Vallehermoso": (40.4435, -3.7072),
    "Chamberí": (40.4380, -3.7010),

    # Fuencarral-El Pardo
    "El Pardo": (40.5170, -3.7750),
    "Fuentelarreina": (40.4952, -3.7350),
    "Peñagrande": (40.4895, -3.7250),
    "Pilar": (40.4835, -3.7138),
    "El Pilar": (40.4835, -3.7138),
    "La Paz": (40.4835, -3.7005),
    "Valverde": (40.4895, -3.7138),
    "Mirasierra": (40.4952, -3.7138),
    "Sanchinarro": (40.4952, -3.6763),
    "Hortaleza - Sanchinarro": (40.4952, -3.6763),
    "Las Tablas": (40.5010, -3.6650),
    "Montecarmelo": (40.5067, -3.7005),

    # Moncloa-Aravaca
    "Casa de Campo": (40.4168, -3.7480),
    "Argüelles": (40.4318, -3.7200),
    "Ciudad Universitaria": (40.4490, -3.7350),
    "Valdezarza": (40.4490, -3.7250),
    "Valdemarín": (40.4547, -3.7650),
    "El Plantío": (40.4605, -3.7950),
    "Aravaca": (40.4547, -3.7850),
    "Moncloa - Aravaca": (40.4435, -3.7480),
    "Moncloa - Aravaca - Aravaca": (40.4547, -3.7850),

    # Latina
    "Los Cármenes": (40.3942, -3.7480),
    "Puerta del Ángel": (40.4007, -3.7350),
    "Lucero": (40.3942, -3.7350),
    "Latina - Lucero": (40.3942, -3.7350),
    "Aluche": (40.3835, -3.7480),
    "Campamento": (40.3770, -3.7650),
    "Cuatro Vientos": (40.3713, -3.7850),
    "Las Águilas": (40.3770, -3.7480),
    "Latina": (40.3942, -3.7420),

    # Carabanchel
    "Comillas": (40.3835, -3.7350),
    "Opañel": (40.3770, -3.7250),
    "San Isidro": (40.3713, -3.7138),
    "Vista Alegre": (40.3835, -3.7138),
    "Pradolongo": (40.3770, -3.7072),
    "Orcasitas": (40.3483, -3.6970),
    "Carabanchel": (40.3780, -3.7270),

    # Usera
    "Orcasitas - Pradolongo": (40.3770, -3.7072),
    "Moscardó": (40.3895, -3.7072),
    "Pradolongo - Usera": (40.3830, -3.7010),
    "Usera": (40.3895, -3.7038),
    "San Andrés": (40.3895, -3.7105),
    "Almendrales": (40.3942, -3.7005),

    # Puente de Vallecas
    "Entrevías": (40.3835, -3.6707),
    "San Diego": (40.3895, -3.6763),
    "Palomeras Bajas": (40.3895, -3.6650),
    "Puente de Vallecas - Palomeras Bajas": (40.3895, -3.6650),
    "Palomeras Sureste": (40.3835, -3.6594),
    "Portazgo": (40.3942, -3.6650),
    "Numancia": (40.3942, -3.6707),
    "Vallecas": (40.3895, -3.6700),

    # Moratalaz
    "Pavones": (40.4007, -3.6538),
    "Horcajo": (40.4007, -3.6481),
    "Marroquina": (40.4063, -3.6538),
    "Media Legua": (40.4063, -3.6481),
    "Fontarrón": (40.3951, -3.6538),
    "Vinateros": (40.3951, -3.6481),

    # Ciudad Lineal
    "Ventas": (40.4260, -3.6594),
    "Pueblo Nuevo": (40.4318, -3.6538),
    "Ciudad Lineal - Pueblo Nuevo": (40.4318, -3.6538),
    "Quintana": (40.4378, -3.6538),
    "La Concepción": (40.4378, -3.6594),
    "San Pascual": (40.4435, -3.6538),
    "San Juan Bautista": (40.4435, -3.6594),
    "Colina": (40.4490, -3.6538),
    "Atalaya": (40.4490, -3.6594),
    "Costillares": (40.4547, -3.6538),
    "Ciudad Lineal": (40.4380, -3.6560),

    # Hortaleza
    "Palomas": (40.4835, -3.6594),
    "Valdefuentes": (40.4895, -3.6481),
    "Canillas": (40.4663, -3.6538),
    "Piovera": (40.4835, -3.6481),
    "Hortaleza": (40.4750, -3.6540),

    # Barajas
    "Alameda de Osuna": (40.4663, -3.6200),
    "Aeropuerto": (40.4895, -3.5707),
    "Casco H. de Barajas": (40.4778, -3.5820),
    "Timón": (40.4663, -3.6094),
    "Corralejos": (40.4835, -3.6094),

    # San Blas-Canillejas
    "Simancas": (40.4318, -3.6200),
    "Hellín": (40.4378, -3.6094),
    "Amposta": (40.4435, -3.6094),
    "Arcos": (40.4435, -3.6200),
    "Rosas": (40.4378, -3.6307),
    "Rejas": (40.4490, -3.5993),
    "San Blas-Canillejas - Rejas": (40.4490, -3.5993),
    "Canillejas": (40.4435, -3.6094),
    "Salvador": (40.4378, -3.6200),
    "San Blas": (40.4380, -3.6180),

    # Villa de Vallecas
    "Casco H. de Vallecas": (40.3713, -3.6307),
    "Santa Eugenia": (40.3713, -3.6200),
    "Villa de Vallecas": (40.3713, -3.6250),

    # Vicálvaro
    "Casco H. de Vicálvaro": (40.3942, -3.6094),
    "Valdebernardo": (40.3895, -3.5993),
    "Valderribas": (40.3835, -3.5993),
    "El Cañaveral": (40.3770, -3.5820),
    "Vicálvaro": (40.3900, -3.6050),
}

# Variantes normalizadas — para matching flexible
def _normalize(s: str) -> str:
    return s.lower().strip().replace("á","a").replace("é","e").replace("í","i")\
            .replace("ó","o").replace("ú","u").replace("ñ","n")

_NORMALIZED = {_normalize(k): v for k, v in BARRIOS_MADRID.items()}


def lookup(neighborhood: str) -> tuple[float, float] | None:
    """Busca coordenadas para un barrio. Primero exacto, luego normalizado, luego parcial."""
    if not neighborhood:
        return None

    # 1. Match exacto
    if neighborhood in BARRIOS_MADRID:
        return BARRIOS_MADRID[neighborhood]

    # 2. Match normalizado (sin tildes, lowercase)
    norm = _normalize(neighborhood)
    if norm in _NORMALIZED:
        return _NORMALIZED[norm]

    # 3. Match parcial — buscar si alguna clave conocida está contenida en el barrio
    for key, coords in _NORMALIZED.items():
        if key in norm or norm in key:
            return coords

    return None


def enrich_locations() -> None:
    db = SessionLocal()
    try:
        listings = (
            db.query(Listing)
            .filter(
                Listing.lat.is_(None),
                (Listing.neighborhood.isnot(None)) | (Listing.district.isnot(None)),
            )
            .all()
        )

        print(f"[enrich_location] {len(listings)} listings sin coordenadas")
        if not listings:
            return

        found = 0
        not_found = []

        for listing in listings:
            key = listing.neighborhood or listing.district
            coords = lookup(key)

            if coords:
                listing.lat, listing.lon = coords
                found += 1
            else:
                not_found.append(key)

        db.commit()
        print(f"[enrich_location] ✓ {found} geocodificados")

        if not_found:
            unique = sorted(set(not_found))
            print(f"[enrich_location] ✗ {len(unique)} barrios no encontrados:")
            for b in unique:
                print(f"  - {b}")

    finally:
        db.close()


if __name__ == "__main__":
    enrich_locations()
