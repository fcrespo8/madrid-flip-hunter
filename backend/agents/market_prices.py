"""
Precios medios €/m² por distrito y barrio en Madrid.
Fuente: Idealista — https://www.idealista.com/sala-de-prensa/informes-precio-vivienda/venta/
Última actualización: abril 2026
RECORDATORIO: actualizar cada 2 meses aproximadamente.
"""

# {barrio/distrito: precio_m2}
PRECIOS_MADRID: dict[str, float] = {
    # ARGANZUELA — 6.334
    "Arganzuela": 6334, "Acacias": 6488, "Chopera": 5882, "Delicias": 6246,
    "Imperial": 6560, "Legazpi": 6639, "Palos de Moguer": 6373,
    # BARAJAS — 4.901
    "Barajas": 4901, "Alameda de Osuna": 5368,
    # CARABANCHEL — 3.718
    "Carabanchel": 3718, "Abrantes": 3347, "Buena Vista": 3363,
    "Comillas": 4625, "Opañel": 4050, "Pau de Carabanchel": 4563,
    "Puerta Bonita": 3504, "San Isidro": 3827, "Vista Alegre": 3656,
    # CENTRO — 7.566
    "Centro": 7566, "Chueca-Justicia": 9150, "Chueca": 9150, "Justicia": 9150,
    "Huertas-Cortes": 8207, "Cortes": 8207,
    "Lavapiés-Embajadores": 6176, "Lavapiés": 6176, "Embajadores": 6176,
    "Malasaña-Universidad": 7834, "Malasaña": 7834, "Universidad": 7834,
    "Palacio": 7653, "Sol": 8015,
    # CHAMARTÍN — 8.187
    "Chamartín": 8187, "Bernabéu-Hispanoamérica": 8587, "Hispanoamérica": 8587,
    "Castilla": 7249, "Ciudad Jardín": 7346, "El Viso": 9585,
    "Nueva España": 8369, "Prosperidad": 7570,
    # CHAMBERÍ — 8.992
    "Chamberí": 8992, "Almagro": 10503, "Arapiles": 8537,
    "Gaztambide": 8118, "Nuevos Ministerios-Ríos Rosas": 8810, "Ríos Rosas": 8810,
    "Trafalgar": 9365, "Vallehermoso": 7921,
    # CIUDAD LINEAL — 5.096
    "Ciudad Lineal": 5096, "Colina": 6934, "Concepción": 5501,
    "Costillares": 6037, "Pueblo Nuevo": 4440, "Quintana": 5124,
    "San Juan Bautista": 6764, "San Pascual": 5999, "Ventas": 4711,
    # FUENCARRAL — 5.423
    "Fuencarral": 5423, "La Paz": 5754, "Las Tablas": 5263,
    "Mirasierra": 6345, "Montecarmelo": 6553, "Peñagrande": 5154,
    "Pilar": 5543, "El Pilar": 5543, "Tres Olivos - Valverde": 4296, "Valverde": 4296,
    # HORTALEZA — 5.411
    "Hortaleza": 5411, "Apóstol Santiago": 4962, "Canillas": 5041,
    "Conde Orgaz-Piovera": 6364, "Palomas": 5489, "Pinar del Rey": 4881,
    "Sanchinarro": 5921, "Valdebebas - Valdefuentes": 5656, "Valdefuentes": 5656,
    # LATINA — 3.916
    "Latina": 3916, "Águilas": 3677, "Aluche": 3671, "Campamento": 3685,
    "Los Cármenes": 3994, "Lucero": 3981, "Puerta del Ángel": 4526,
    # MONCLOA — 6.376
    "Moncloa": 6376, "Aravaca": 5619, "Argüelles": 7955,
    "Casa de Campo": 6514, "Ciudad Universitaria": 6132,
    "El Plantío": 4023, "Valdemarín": 6026, "Valdezarza": 5137,
    # MORATALAZ — 4.533
    "Moratalaz": 4533, "Fontarrón": 4166, "Marroquina": 5000,
    "Media Legua": 4720, "Vinateros": 4528,
    # PUENTE DE VALLECAS — 3.325
    "Puente de Vallecas": 3325, "Vallecas": 3325,
    "Entrevías": 2873, "Numancia": 3540, "Palomeras Bajas": 3395,
    "Palomeras Sureste": 3360, "Portazgo": 3290, "San Diego": 3389,
    # RETIRO — 7.758
    "Retiro": 7758, "Adelfas": 6154, "Estrella": 6786,
    "Ibiza": 9488, "Jerónimos": 7905, "Niño Jesús": 7570, "Pacífico": 6786,
    # SALAMANCA — 10.189
    "Salamanca": 10189, "Castellana": 11387, "Fuente del Berro": 8006,
    "Goya": 10440, "Guindalera": 7524, "Lista": 10640, "Recoletos": 11276,
    # SAN BLAS — 4.051
    "San Blas": 4051, "Amposta": 3378, "Arcos": 3683, "Canillejas": 3903,
    "Hellín": 3590, "Rejas": 4330, "Rosas": 5089, "Salvador": 5598, "Simancas": 4093,
    # TETUÁN — 6.034
    "Tetuán": 6034, "Bellas Vistas": 5559, "Berruguete": 5378,
    "Cuatro Caminos": 7394, "Cuzco-Castillejos": 7333,
    "Valdeacederas": 5282, "Ventilla-Almenara": 5214,
    # USERA — 3.609
    "Usera": 3609, "12 de Octubre-Orcasur": 2618, "Almendrales": 3961,
    "Moscardó": 3923, "Orcasitas": 2888, "Pradolongo": 3599,
    "San Fermín": 3461, "Zofío": 3734,
    # VICÁLVARO — 3.903
    "Vicálvaro": 3903, "Ambroz": 3699,
    "El Cañaveral - Los Berrocales": 4306, "Valdebernardo - Valderribas": 4353,
    # VILLA DE VALLECAS — 3.717
    "Villa de Vallecas": 3717, "Casco Histórico de Vallecas": 3281,
    "Ensanche de Vallecas - La Gavia": 4225, "Santa Eugenia": 3839,
    # VILLAVERDE — 3.002
    "Villaverde": 3002, "Butarque": 3461, "Los Ángeles": 3266,
    "San Andrés": 2917, "San Cristóbal": 2308,
}


def _normalize(s: str) -> str:
    return (s.lower().strip()
            .replace("á","a").replace("é","e").replace("í","i")
            .replace("ó","o").replace("ú","u").replace("ñ","n"))


_NORMALIZED = {_normalize(k): v for k, v in PRECIOS_MADRID.items()}


def get_market_price(neighborhood: str | None, district: str | None = None) -> float | None:
    """Devuelve precio medio €/m² para un barrio o distrito. Matching flexible."""
    for key in [neighborhood, district]:
        if not key:
            continue
        # Exacto
        if key in PRECIOS_MADRID:
            return PRECIOS_MADRID[key]
        # Normalizado
        norm = _normalize(key)
        if norm in _NORMALIZED:
            return _NORMALIZED[norm]
        # Parcial
        for k, v in _NORMALIZED.items():
            if k in norm or norm in k:
                return v
    return None
