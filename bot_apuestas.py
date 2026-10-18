import requests
from datetime import datetime

# ==========================================
# 1. TUS CLAVES DE CONFIGURACIÓN (YA LISTAS)
# ==========================================
ODDS_API_KEY = "5132116bf9df0f4636b614c029c7a690"
TELEGRAM_TOKEN = "8688601863:AAEVBvC8kRQ7fk_gFVBfya38ZogrhGVOw4w"
CHAT_ID = "799317842"

# Ligas que vamos a escanear
DEPORTES = [
    {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga (España)"},
    {"key": "soccer_epl", "nombre": "⚽ Premier League (Inglaterra)"},
    {"key": "basketball_euroleague", "nombre": "🏀 Euroliga (Baloncesto)"}
]

# Parámetros matemáticos del filtro
UMBRAL_VALOR_MINIMO = 2.0   # Mínimo % de ventaja matemática para avisar
CUOTA_MINIMA = 1.25         # Ignorar cuotas ridículamente bajas
CUOTA_MAXIMA = 5.00         # Tope máximo acordado (evitar "milagros" imposibles)

# Palabras clave de casas que operan en España (ignora NordicBet, Tipico, Unibet NL, etc.)
CASAS_ESPANA = [
    "winamax",
    "william hill",
    "betfair",
    "marathon",
    "888sport",
    "betsson",
    "casumo",
    "bwin",
    "sportium",
    "codere"
]


def es_casa_espanola(key_casa, titulo_casa):
    """Filtra solo las casas de apuestas relevantes en España."""
    texto = f"{key_casa} {titulo_casa}".lower()
    if "nl" in texto or "uk" in texto or "se" in texto:
        # Evitar versiones locales exclusivas de Holanda/Suecia como Unibet (NL)
        if "unibet" in texto:
            return False
    return any(casa in texto for casa in CASAS_ESPANA)


def enviar_telegram(mensaje):
    """Envía un mensaje formateado a tu Telegram personal."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    datos = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        respuesta = requests.post(url, json=datos, timeout=15)
        if respuesta.status_code != 200:
            print(f"Error al enviar a Telegram: {respuesta.text}")
    except Exception as e:
        print(f"Error de conexión con Telegram: {e}")


def analizar_ligas():
    """Escanea las cuotas en tiempo real, agrupa por partido y filtra hasta @5.00."""
    alertas_agrupadas = []
    referencias_bet365 = []
    peticiones_restantes = "Desconocido"

    for deporte in DEPORTES:
        url = f"https://api.the-odds-api.com/v4/sports/{deporte['key']}/odds/"
        parametros = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal"
        }

        try:
            res = requests.get(url, params=parametros, timeout=20)
            peticiones_restantes = res.headers.get("x-requests-remaining", peticiones_restantes)

            if res.status_code != 200:
                print(f"Aviso en {deporte['nombre']}: {res.text}")
                continue

            partidos = res.json()
        except Exception as e:
            print(f"Error conectando con la API en {deporte['nombre']}: {e}")
            continue

        # Analizar los próximos 8 partidos de cada competición
        for partido in partidos[:8]:
            local = partido.get("home_team")
            visitante = partido.get("away_team")
            fecha_iso =
