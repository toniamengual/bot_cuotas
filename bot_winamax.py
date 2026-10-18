"""Radar Winamax: apuestas simples y combinadas dobles con valor, pensado para GitHub Actions."""
import html
import json
import os
import time
from datetime import datetime, timedelta, timezone
from itertools import combinations
from zoneinfo import ZoneInfo

import requests

# ==========================================
# 1. CONFIGURACIÓN
# Las claves NO van aquí: se leen de los "Secrets" de GitHub (o de variables de entorno).
# ==========================================
ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
BANKROLL = float(os.environ.get("BANKROLL", "1000"))   # tu banca en €

ZONA = ZoneInfo("Europe/Madrid")
ESTADO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado.json")

# Simples
UMBRAL_SIMPLE = 2.5          # % mínimo de ventaja para avisar
CUOTA_MIN = 1.25
CUOTA_MAX = 5.00

# Combinadas dobles
CUOTA_MAX_PATA = 2.85        # cuota máxima de cada pata (favoritos)
EDGE_MIN_PATA = 0.0          # cada pata debe tener valor igual o mayor que 0
EDGE_MIN_COMBI = 4.0         # % mínimo de ventaja de la combinada
CUOTA_MAX_COMBI = 7.00
CANDIDATAS = 8

# Banca
KELLY_FRACCION = 0.25        # se apuesta un cuarto de lo que sugiere Kelly
STAKE_MAX_PCT = 3.0          # nunca más del 3% de la banca en una apuesta
COMBI_FACTOR = 0.5           # en combinadas se apuesta la mitad

HORAS_VENTANA = 48           # solo partidos de las próximas 48 h
CREDITOS_MINIMOS = 20
EDGE_SOSPECHOSO = 12.0

CASAS_SHARP = ["pinnacle", "betfair_ex_eu", "matchbook"]   # referencia de "precio real"
CASA_OBJETIVO = "winamax"    # coincide con winamax_fr y winamax_de


# ==========================================
# 2. MATEMÁTICAS
# ==========================================
def quitar_margen(cuotas):
    """Quita el margen de la casa con el método de potencia (más fiable que el proporcional)."""
    p = [1.0 / c for c in cuotas]
    if sum(p) <= 1.0:
        s = sum(p)
        return [x / s for x in p]
    lo, hi = 1.0, 30.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if sum(x ** mid for x in p) > 1.0:
            lo = mid
        else:
            hi = mid
    r = [x ** hi for x in p]
    s = sum(r)
    return [x / s for x in r]


def kelly_stake(prob, cuota, factor=KELLY_FRACCION):
    if cuota <= 1:
        return 0.0
    k = (prob * cuota - 1.0) / (cuota - 1.0)
    pct = max(0.0, min(k * factor * 100.0, STAKE_MAX_PCT))
    return round(BANKROLL * pct / 100.0, 2)


def cuotas_h2h(casa):
    for m in casa.get("markets", []):
        if m.get("key") == "h2h":
            return {o["name"]: o["price"] for o in m["outcomes"]}
    return {}


def prob_justa(casas):
    """Probabilidad real de cada resultado: media de las casas de referencia disponibles."""
    lista = []
    for c in casas:
        if c["key"] in CASAS_SHARP:
            cu = cuotas_h2h(c)
            if len(cu) >= 2:
                nombres = sorted(cu)
                lista.append(dict(zip(nombres, quitar_margen([cu[n] for n in nombres]))))
    if not lista:
        return {}
    nombres = set(lista[0])
    lista = [d for d in lista if set(d) == nombres]
    return {n: sum(d[n] for d in lista) / len(lista) for n in nombres}


def mejores_cuotas_winamax(casas):
    """Mejor cuota de cada resultado entre las versiones de Winamax que devuelve la API."""
    mejor = {}
    for c in casas:
        if CASA_OBJETIVO in (c["key"] + c["title"]).lower():
            for sel, cuota in cuotas_h2h(c).items():
                if sel not in mejor or cuota > mejor[sel]:
                    mejor[sel] = cuota
    return mejor


# ==========================================
# 3. LIGAS DEL DÍA, API Y TELEGRAM
# ==========================================
def deportes_hoy():
    """Elige las ligas según el día para ahorrar créditos de la API."""
    if datetime.now(ZONA).weekday() in (1, 2, 3):
        return [
            {"key": "soccer_uefa_champs_league", "nombre": "🏆 Champions League"},
            {"key": "basketball_euroleague", "nombre": "🏀 Euroliga"},
            {"key": "basketball_nba", "nombre": "🏀 NBA"},
            {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga"},
        ]
    return [
        {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga"},
        {"key": "soccer_epl", "nombre": "⚽ Premier League"},
        {"key": "soccer_italy_serie_a", "nombre": "⚽ Serie A"},
        {"key": "basketball_euroleague", "nombre": "🏀 Euroliga"},
    ]


def obtener_cuotas(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": "h2h", "oddsFormat": "decimal"}
    for intento in range(3):
        try:
            res = requests.get(url, params=params, timeout=20)
            if res.status_code == 200:
                return res.json(), res.headers
            print(f"Aviso {sport_key}: HTTP {res.status_code} {res.text[:200]}")
            if res.status_code in (401, 422, 429):
                break
        except requests.RequestException as e:
            print(f"Error {sport_key}: {e}")
        time.sleep(2 * (intento + 1))
    return None, {}


def trocear(texto, limite=4000):
    partes, actual = [], ""
    for bloque in texto.split("\n\n"):
        if actual and len(actual) + len(bloque) + 2 > limite:
            partes.append(actual)
            actual = ""
        actual += ("\n\n" if actual else "") + bloque
    if actual:
        partes.append(actual)
    return partes


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for parte in trocear(mensaje):
        try:
            res = requests.post(url, json={"chat_id": CHAT_ID, "text": parte, "parse_mode": "HTML",
                                           "disable_web_page_preview": True}, timeout=15)
            if res.status_code != 200:
                print(f"Error Telegram: {res.text}")
        except requests.RequestException as e:
            print(f"Error de conexión: {e}")


# ==========================================
# 4. ESTADO (evita avisos repetidos entre ejecuciones)
# ==========================================
def cargar_estado():
    try:
        with open(ESTADO_PATH, encoding="utf-8") as f:
            estado = json.load(f)
    except (OSError, ValueError):
        estado = {}
    corte = (datetime.now(timezone.utc) - timedelta(days=3)).timestamp()
    return {k: t for k, t in estado.items() if t > corte}


def guardar_estado(estado):
    with open(ESTADO_PATH, "w", encoding="utf-8") as f:
        json.dump(estado, f, indent=1)


def es_nuevo(estado, ident):
    if ident in estado:
        return False
    estado[ident] = datetime.now(timezone.utc).timestamp()
    return True


# ==========================================
# 5. ANÁLISIS
# ==========================================
def esc(t):
    return html.escape(str(t))


def nombre_sel(sel):
    return "Empate" if sel == "Draw" else sel


def analizar():
    estado = cargar_estado()
    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(hours=HORAS_VENTANA)
    simples, candidatas = [], []
    restantes = "Desconocido"

    for dep in deportes_hoy():
        if restantes != "Desconocido" and float(restantes) < CREDITOS_MINIMOS:
            print("Quedan pocos créditos; se detiene.")
            break
        partidos, headers = obtener_cuotas(dep["key"])
        restantes = headers.get("x-requests-remaining", restantes)
        if not partidos:
            continue

        for p in sorted(partidos, key=lambda x: x["commence_time"]):
            dt = datetime.fromisoformat(p["commence_time"].replace("Z", "+00:00"))
            if not (ahora < dt <= limite):
                continue
            casas = p.get("bookmakers", [])
            prob = prob_justa(casas)
            cuotas_w = mejores_cuotas_winamax(casas)
            if not prob or set(cuotas_w) != set(prob):
                continue

            fecha = dt.astimezone(ZONA).strftime("%d/%m %H:%M")
            partido = f"{p['home_team']} vs {p['away_team']}"
            mejor_pata = None
            for sel, cuota in cuotas_w.items():
                pr = prob[sel]
                edge = (pr * cuota - 1.0) * 100.0
                datos = {"id": p["id"], "partido": partido, "liga": dep["nombre"], "fecha": fecha,
                         "sel": nombre_sel(sel), "cuota": cuota, "prob": pr, "edge": edge}
                if CUOTA_MIN <= cuota <= CUOTA_MAX and edge >= UMBRAL_SIMPLE:
                    simples.append(datos)
                if CUOTA_MIN <= cuota <= CUOTA_MAX_PATA and edge >= EDGE_MIN_PATA:
                    if not mejor_pata or edge > mejor_pata["edge"]:
                        mejor_pata = datos
            if mejor_pata:
                candidatas.append(mejor_pata)

    # --- Simples ---
    simples.sort(key=lambda x: x["edge"], reverse=True)
    bloques_simples = []
    for s in simples:
        if not es_nuevo(estado, f"S|{s['id']}|{s['sel']}|{s['cuota']}"):
            continue
        aviso = "\n⚠️ <i>Ventaja muy alta: comprueba que la cuota sigue disponible.</i>" if s["edge"] > EDGE_SOSPECHOSO else ""
        bloques_simples.append(
            f"🎯 <b>WINAMAX +{s['edge']:.1f}%</b> · {esc(s['liga'])} · ⏰ {s['fecha']}h\n"
            f"⚔️ <b>{esc(s['partido'])}</b>\n"
            f"👉 <b>{esc(s['sel'])}</b> a cuota <b>@{s['cuota']}</b> "
            f"(cuota justa @{1 / s['prob']:.2f})\n"
            f"💶 Stake sugerido: <b>{kelly_stake(s['prob'], s['cuota']):.2f}€</b>{aviso}")

    # --- Combinadas dobles (patas de partidos distintos, ambas en Winamax) ---
    candidatas.sort(key=lambda x: x["edge"], reverse=True)
    combis = []
    for a, b in combinations(candidatas[:CANDIDATAS], 2):
        if a["id"] == b["id"]:
            continue
        cuota = round(a["cuota"] * b["cuota"], 2)
        pr = a["prob"] * b["prob"]
        edge = (pr * cuota - 1.0) * 100.0
        if edge >= EDGE_MIN_COMBI and cuota <= CUOTA_MAX_COMBI:
            combis.append((edge, a, b, cuota, pr))
    combis.sort(key=lambda x: x[0], reverse=True)

    bloques_combis = []
    for edge, a, b, cuota, pr in combis[:3]:
        if not es_nuevo(estado, f"C|{a['id']}|{a['sel']}|{a['cuota']}|{b['id']}|{b['sel']}|{b['cuota']}"):
            continue
        bloques_combis.append(
            f"🔗 <b>DOBLE WINAMAX +{edge:.1f}%</b>\n"
            f"1️⃣ {esc(a['partido'])} ({a['fecha']}h) 👉 <b>{esc(a['sel'])}</b> @{a['cuota']}\n"
            f"2️⃣ {esc(b['partido'])} ({b['fecha']}h) 👉 <b>{esc(b['sel'])}</b> @{b['cuota']}\n"
            f"💰 Cuota total <b>@{cuota}</b> · prob. real {pr * 100:.1f}%\n"
            f"💶 Stake sugerido: <b>{kelly_stake(pr, cuota, KELLY_FRACCION * COMBI_FACTOR):.2f}€</b>")

    guardar_estado(estado)

    if not (bloques_simples or bloques_combis):
        print("Sin novedades; no se envía mensaje.")
        return

    partes = ["🤖 <b>RADAR WINAMAX</b>"]
    if bloques_simples:
        partes.append("🚨 <b>APUESTAS SIMPLES CON VALOR</b>")
        partes += bloques_simples[:5]
    if bloques_combis:
        partes.append("🔥 <b>COMBINADAS DOBLES CON VALOR</b>\n"
                      "<i>Mismas casa y cuota en las dos patas; más riesgo que una simple.</i>")
        partes += bloques_combis
    partes.append(f"🔋 <i>Créditos restantes: {restantes}</i>")
    enviar_telegram("\n\n".join(partes))


if __name__ == "__main__":
    if not (ODDS_API_KEY and TELEGRAM_TOKEN and CHAT_ID):
        raise SystemExit("Faltan los secrets ODDS_API_KEY, TELEGRAM_TOKEN y/o CHAT_ID.")
    analizar()
