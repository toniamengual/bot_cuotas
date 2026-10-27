"""Radar Winamax: apuestas simples y combinadas dobles con valor, pensado para GitHub Actions."""
import difflib
import html
import json
import math
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from itertools import combinations
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

# ==========================================
# 1. CONFIGURACIÓN
# Las claves NO van aquí: se leen de los "Secrets" de GitHub (o de variables de entorno).
# Puedes poner varias claves separadas por coma en ODDS_API_KEY o usar ODDS_API_KEY_2, ODDS_API_KEY_3.
# ==========================================
_raw_keys = ",".join(filter(None, [
    os.environ.get("ODDS_API_KEY", ""),
    os.environ.get("ODDS_API_KEY_2", ""),
    os.environ.get("ODDS_API_KEY_3", ""),
]))
ODDS_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
ODDS_API_KEY = ODDS_API_KEYS[0] if ODDS_API_KEYS else ""
_key_idx = 0
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("CHAT_ID", "")
BANKROLL = float(os.environ.get("BANKROLL", "1000"))   # tu banca en €
ENVIAR_RESUMEN = os.environ.get("RESUMEN", "").lower() == "true"   # mensaje de prueba aunque no haya avisos

ZONA = ZoneInfo("Europe/Madrid")
ESTADO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado.json")
APUESTAS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "apuestas.json")
ESPERA_RESULTADO_H = 3       # horas tras el inicio antes de buscar el resultado
DIAS_ANULAR = 4              # si no hay resultado tras estos días, la apuesta se anula

# Simples y Otras casas españolas
UMBRAL_SIMPLE = 1.0          # % mínimo de ventaja para avisar en Winamax (antes 2.5%)
UMBRAL_OTRAS = 2.0           # % mínimo de ventaja para avisar en otras casas de España
CUOTA_MIN = 1.25
CUOTA_MAX = 5.00

# Combinadas dobles (en Winamax)
CUOTA_MAX_PATA = 2.85        # cuota máxima de cada pata (favoritos)
EDGE_MIN_PATA = -1.0         # permite patas cercanas al precio justo (-1.0%) si la otra pata compensa
EDGE_MIN_COMBI = 1.0         # % mínimo de ventaja de la combinada doble (antes 2.0%)
CUOTA_MAX_COMBI = 7.00
CANDIDATAS = 10

# Banca
KELLY_FRACCION = 0.25        # se apuesta un cuarto de lo que sugiere Kelly
STAKE_MAX_PCT = 3.0          # nunca más del 3% de la banca en una apuesta
COMBI_FACTOR = 0.5           # en combinadas se apuesta la mitad

HORAS_VENTANA = 72           # solo partidos de las próximas 72 h
CREDITOS_MINIMOS = 20
EDGE_SOSPECHOSO = 12.0        # por encima se avisa con una advertencia
EDGE_DESCARTE = 20.0         # por encima se descarta: casi seguro es un error de datos
DISCREPANCIA_MAX = 0.05      # una casa de referencia que difiera >5 puntos de Pinnacle se ignora

# Ligas en orden de prioridad. Las que no estén activas o no tengan partidos se saltan sin gastar créditos.
LIGAS = [
    {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga"},
    {"key": "soccer_epl", "nombre": "⚽ Premier League"},
    {"key": "soccer_uefa_champs_league", "nombre": "🏆 Champions League"},
    {"key": "soccer_italy_serie_a", "nombre": "⚽ Serie A"},
    {"key": "soccer_germany_bundesliga", "nombre": "⚽ Bundesliga"},
    {"key": "soccer_france_ligue_one", "nombre": "⚽ Ligue 1"},
    {"key": "basketball_euroleague", "nombre": "🏀 Euroliga"},
    {"key": "basketball_nba", "nombre": "🏀 NBA"},
    {"key": "soccer_uefa_europa_league", "nombre": "⚽ Europa League"},
    {"key": "soccer_spain_segunda_division", "nombre": "⚽ Segunda División"},
    {"key": "soccer_netherlands_eredivisie", "nombre": "⚽ Eredivisie"},
    {"key": "soccer_portugal_primeira_liga", "nombre": "⚽ Primeira Liga"},
    {"key": "icehockey_nhl", "nombre": "🏒 NHL"},
]
INCLUIR_TENIS = True         # añade los torneos de tenis activos (al final de la prioridad)

# Control de créditos de la API (h2h+totals cuesta 2 créditos por liga; solo h2h en tenis cuesta 1)
MERCADOS = "h2h,totals"      # mercados a analizar (1X2 + Más/Menos goles o puntos)
MAX_LIGAS_EJECUCION = 8      # tope base de ligas por ejecución y por clave
RESERVA_FACTOR = 0.85        # usa solo el 85% de los créditos; el resto para buscar resultados
EJECUCIONES_DIA = 4          # número de ejecuciones diarias en tu cron externo
DIA_RENOVACION = 1           # día del mes (1-28) en que se renuevan tus créditos

# Noticias en los avisos (Google News RSS; no gasta créditos)
INCLUIR_NOTICIAS = True
NOTICIAS_POR_EQUIPO = 2      # titulares por equipo en las apuestas simples

CASAS_SHARP = ["pinnacle", "betfair_ex_eu", "matchbook"]   # referencia de "precio real"
CASA_OBJETIVO = "winamax"    # coincide con winamax_fr y winamax_de
OTRAS_CASAS_ESPANA = [
    "williamhill", "william hill", "888sport", "betsson",
    "bwin", "codere", "sportium", "marathon", "casumo", "leovegas",
]


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


def lineas_casa(casa):
    """Extrae {(mercado, punto): {seleccion: cuota}} de una casa (h2h y totals)."""
    res = {}
    for m in casa.get("markets", []):
        k = m.get("key")
        if k == "h2h":
            cu = {o["name"]: o["price"] for o in m.get("outcomes", [])}
            if len(cu) >= 2:
                res[("h2h", None)] = cu
        elif k == "totals":
            por_pt = {}
            for o in m.get("outcomes", []):
                pt = o.get("point")
                if pt is not None and o.get("name") in ("Over", "Under"):
                    por_pt.setdefault(float(pt), {})[o["name"]] = o["price"]
            for pt, cu in por_pt.items():
                if len(cu) == 2:
                    res[("totals", pt)] = cu
    return res


def prob_justa(casas):
    """Probabilidad real por línea {(mercado, punto): {sel: prob}}.

    Se ancla en Pinnacle (obligatorio). Betfair Exchange / Matchbook solo se promedian si coinciden
    con Pinnacle (diferencia máxima DISCREPANCIA_MAX); las líneas poco líquidas dan cuotas absurdas.
    """
    por_linea = {}
    for c in casas:
        if c["key"] in CASAS_SHARP:
            for linea, cu in lineas_casa(c).items():
                nombres = sorted(cu)
                por_linea.setdefault(linea, {})[c["key"]] = dict(
                    zip(nombres, quitar_margen([cu[n] for n in nombres]))
                )
    justas = {}
    for linea, por_casa in por_linea.items():
        pin = por_casa.get("pinnacle")
        if not pin:
            continue
        lista = [pin]
        for clave, d in por_casa.items():
            if clave == "pinnacle" or set(d) != set(pin):
                continue
            if max(abs(d[n] - pin[n]) for n in pin) <= DISCREPANCIA_MAX:
                lista.append(d)
            else:
                print(f"Referencia {clave} descartada en {linea}: discrepa de Pinnacle")
        justas[linea] = {n: sum(d[n] for d in lista) / len(lista) for n in pin}
    return justas


def mejores_cuotas_winamax(casas):
    """Mejor cuota de cada línea {(mercado, punto): {sel: cuota}} entre las versiones de Winamax."""
    mejor = {}
    for c in casas:
        if CASA_OBJETIVO in (c["key"] + c["title"]).lower():
            for linea, cu in lineas_casa(c).items():
                dest = mejor.setdefault(linea, {})
                for sel, cuota in cu.items():
                    if sel not in dest or cuota > dest[sel]:
                        dest[sel] = cuota
    return mejor


def mejores_cuotas_otras_espana(casas):
    """{(mercado, punto): {sel: (cuota, nombre_casa)}} con la mejor cuota entre las demás casas españolas."""
    mejor = {}
    for c in casas:
        texto = f"{c['key']} {c['title']}".lower()
        if c["key"] in CASAS_SHARP or CASA_OBJETIVO in texto:
            continue
        if not any(k in texto for k in OTRAS_CASAS_ESPANA):
            continue
        for linea, cu in lineas_casa(c).items():
            dest = mejor.setdefault(linea, {})
            for sel, cuota in cu.items():
                if sel not in dest or cuota > dest[sel][0]:
                    dest[sel] = (cuota, c["title"])
    return mejor


def _phi(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _inv_phi(p):
    p = max(1e-6, min(1.0 - 1e-6, p))
    lo, hi = -6.0, 6.0
    for _ in range(50):
        mid = (lo + hi) / 2.0
        if _phi(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def prob_linea_para_casa(justas, linea, sport_key):
    """Devuelve {sel: prob} para `linea`. Si es `totals` en baloncesto y Pinnacle tiene otra línea cercana, interpola."""
    if linea in justas:
        return justas[linea]
    m_key, pt_casa = linea
    if m_key != "totals" or pt_casa is None or not sport_key.startswith("basketball_"):
        return None
    # Solo interpolamos líneas de medio punto (.5) para evitar empates/nulos en números enteros
    if abs((pt_casa * 2) % 2 - 1) > 1e-3:
        return None
    candidatas = [( abs(pt_casa - pt_pin), pt_pin, probs )
                  for (mk, pt_pin), probs in justas.items()
                  if mk == "totals" and pt_pin is not None and abs((pt_pin * 2) % 2 - 1) < 1e-3]
    if not candidatas:
        return None
    dist, pt_pin, probs_pin = min(candidatas, key=lambda x: x[0])
    if dist > 3.5 or "Over" not in probs_pin:
        return None
    sigma = 13.5 if "nba" in sport_key else 12.0
    z_pin = _inv_phi(1.0 - probs_pin["Over"])
    z_casa = z_pin + (pt_casa - pt_pin) / sigma
    p_over = max(0.01, min(0.99, 1.0 - _phi(z_casa)))
    return {"Over": p_over, "Under": 1.0 - p_over}


# ==========================================
# 3. LIGAS DEL DÍA, API Y TELEGRAM
# ==========================================
def _rotar_clave():
    """Pasa a la siguiente clave de ODDS_API_KEYS si hay más disponibles."""
    global _key_idx, ODDS_API_KEY
    if _key_idx + 1 < len(ODDS_API_KEYS):
        _key_idx += 1
        ODDS_API_KEY = ODDS_API_KEYS[_key_idx]
        print(f"Rotando a clave API #{_key_idx + 1} de {len(ODDS_API_KEYS)}")
        return True
    return False


def _sin_creditos(res):
    """True si la respuesta de la API indica clave agotada o inválida (no simple límite por segundo)."""
    if res.status_code == 401:
        return True
    if res.status_code == 429 and "OUT_OF_USAGE_CREDITS" in (res.text or "").upper():
        return True
    rem = res.headers.get("x-requests-remaining")
    return rem is not None and float(rem) < CREDITOS_MINIMOS


def api_get(ruta, params=None):
    """Llamada a The Odds API. Los endpoints /sports y /events NO consumen créditos."""
    for intento in range(3):
        try:
            res = requests.get(f"https://api.the-odds-api.com/v4{ruta}",
                               params={"apiKey": ODDS_API_KEY, **(params or {})}, timeout=20)
            if res.status_code == 200:
                return res.json()
            print(f"Aviso {ruta}: HTTP {res.status_code} {res.text[:150]}")
            if _sin_creditos(res) and _rotar_clave():
                continue
            if res.status_code == 429:
                time.sleep(1.5 * (intento + 1))
                continue
        except requests.RequestException as e:
            print(f"Error {ruta}: {e}")
        return None
    return None


def dias_hasta_renovacion():
    """Días que faltan para que se renueven los créditos de la API."""
    hoy = datetime.now(ZONA).date()
    y, m = hoy.year, hoy.month
    if hoy.day >= DIA_RENOVACION:
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return max(1, (datetime(y, m, DIA_RENOVACION).date() - hoy).days)


def seleccionar_deportes(ahora, limite):
    """Ligas de la lista que están activas y tienen partidos en la ventana (comprobación gratuita)."""
    activos = api_get("/sports")
    candidatos = list(LIGAS)
    if activos:
        por_clave = {s["key"]: s for s in activos if s.get("active")}
        candidatos = [d for d in LIGAS if d["key"] in por_clave]
        if INCLUIR_TENIS:
            candidatos += [{"key": s["key"], "nombre": "🎾 " + s["title"]}
                           for s in por_clave.values() if s.get("group") == "Tennis"]
    elegidos = []
    for d in candidatos:
        time.sleep(0.12)              # evita el límite de peticiones por segundo (HTTP 429)
        eventos = api_get(f"/sports/{d['key']}/events")
        if eventos is None:           # no se pudo comprobar: se incluye por si acaso
            elegidos.append(d)
            continue
        n = 0
        for ev in eventos:
            dt = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00"))
            if ahora < dt <= limite:
                n += 1
        if n:
            elegidos.append(d)
    print("Ligas con partidos:", ", ".join(d["key"] for d in elegidos) or "ninguna")
    return elegidos


def obtener_cuotas(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds/"
    mercados = "h2h" if sport_key.startswith("tennis_") else MERCADOS
    for intento in range(3):
        params = {"apiKey": ODDS_API_KEY, "regions": "eu", "markets": mercados, "oddsFormat": "decimal"}
        try:
            res = requests.get(url, params=params, timeout=20)
            if res.status_code == 200:
                return res.json(), res.headers
            print(f"Aviso {sport_key}: HTTP {res.status_code} {res.text[:200]}")
            if _sin_creditos(res) and _rotar_clave():
                continue
            if res.status_code == 429:
                time.sleep(1.5 * (intento + 1))
                continue
            if res.status_code in (401, 422):
                break
        except requests.RequestException as e:
            print(f"Error {sport_key}: {e}")
        time.sleep(2 * (intento + 1))
    return None, {}


_cache_noticias = {}


def titulares(equipo, n):
    """Últimos titulares (≤3 días) de un equipo desde Google News RSS. No gasta créditos de la API."""
    if not INCLUIR_NOTICIAS:
        return []
    if equipo not in _cache_noticias:
        items = []
        try:
            url = ("https://news.google.com/rss/search?q=" + quote(f'"{equipo}" when:3d')
                   + "&hl=es&gl=ES&ceid=ES:es")
            res = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            if res.status_code == 200:
                for it in ET.fromstring(res.content).iter("item"):
                    t = (it.findtext("title") or "").strip()
                    l = (it.findtext("link") or "").strip()
                    if t and l:
                        items.append((t, l))
                    if len(items) >= 5:
                        break
        except (requests.RequestException, ET.ParseError) as e:
            print(f"Noticias {equipo}: {e}")
        _cache_noticias[equipo] = items
    return _cache_noticias[equipo][:n]


def bloque_noticias(equipos, n):
    lineas = []
    for eq in equipos:
        for t, l in titulares(eq, n):
            lineas.append(f'   • <a href="{html.escape(l, quote=True)}">{html.escape(t)}</a>')
    if not lineas:
        return ""
    return "\n📰 <i>Últimas noticias (revisa lesiones o cambios):</i>\n" + "\n".join(lineas)


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
# 4b. SEGUIMIENTO DE APUESTAS SUGERIDAS (guardar, comprobar resultado, balance)
# Se asume que apuestas exactamente el stake sugerido a la cuota avisada.
# ==========================================
def cargar_json(ruta, defecto):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return defecto


def guardar_json(ruta, datos):
    with open(ruta, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=1, ensure_ascii=False)


def nueva_apuesta(tipo, cuota, prob, edge, stake, patas, casa="Winamax"):
    campos = ("id", "sport", "partido", "sel", "raw", "mercado", "punto", "cuota", "prob", "commence")
    return {"tipo": tipo, "casa": casa, "ts": datetime.now(timezone.utc).timestamp(), "cuota": cuota,
            "prob": prob, "edge": edge, "stake": stake, "estado": "pendiente", "beneficio": None,
            "patas": [{**{k: p.get(k) for k in campos}, "resultado": None, "cierre_prob": None} for p in patas]}


def actualizar_cierre(apuestas, event_id, prob_por_linea, sport_key=""):
    """Mientras el partido no empieza, guarda la última probabilidad justa (aprox. línea de cierre)."""
    ahora = datetime.now(timezone.utc).timestamp()
    for b in apuestas:
        if b["estado"] != "pendiente":
            continue
        for leg in b["patas"]:
            if leg["id"] == event_id and leg["resultado"] is None and leg["commence"] > ahora:
                linea = (leg.get("mercado") or "h2h", leg.get("punto"))
                pr_l = prob_linea_para_casa(prob_por_linea, linea, sport_key or leg.get("sport", ""))
                if pr_l:
                    leg["cierre_prob"] = pr_l.get(leg["raw"])


def obtener_resultados(sport_key):
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
    while True:
        try:
            res = requests.get(url, params={"apiKey": ODDS_API_KEY, "daysFrom": 3}, timeout=20)
            if res.status_code == 200:
                rest = res.headers.get("x-requests-remaining")
                if rest is not None and float(rest) < CREDITOS_MINIMOS:
                    _rotar_clave()
                return {e["id"]: e for e in res.json()}, rest
            print(f"Aviso resultados {sport_key}: HTTP {res.status_code} {res.text[:200]}")
            if res.status_code in (401, 429) and _rotar_clave():
                continue
        except requests.RequestException as e:
            print(f"Error resultados {sport_key}: {e}")
        return {}, None


def _marcador_odds(ev):
    """Extrae (sh, sa, home_team, away_team) de un evento completado de The Odds API, o None."""
    if not ev or not ev.get("completed"):
        return None
    marcador = {}
    for s in ev.get("scores") or []:
        try:
            marcador[s["name"]] = float(s["score"])
        except (KeyError, TypeError, ValueError):
            return None
    h, a = ev.get("home_team"), ev.get("away_team")
    if h not in marcador or a not in marcador:
        return None
    return marcador[h], marcador[a], h, a


def _evaluar_pata(leg, sh, sa, local, visit):
    """Evalúa si una pata ('h2h' o 'totals') es 'won', 'lost' o 'void'."""
    if (leg.get("mercado") or "h2h") == "totals" and leg.get("punto") is not None:
        total = sh + sa
        pt = float(leg["punto"])
        if total == pt:
            return "void"
        return "won" if (leg["raw"] == "Over") == (total > pt) else "lost"
    w = "Draw" if sh == sa else (local if sh > sa else visit)
    return "won" if w == leg["raw"] else "lost"


# Resultados vía ESPN (gratis, sin clave ni créditos). Si falla, se usa The Odds API como respaldo.
ESPN_LIGAS = {
    "soccer_spain_la_liga": "soccer/esp.1",
    "soccer_epl": "soccer/eng.1",
    "soccer_uefa_champs_league": "soccer/uefa.champions",
    "soccer_uefa_europa_league": "soccer/uefa.europa",
    "soccer_italy_serie_a": "soccer/ita.1",
    "soccer_germany_bundesliga": "soccer/ger.1",
    "soccer_france_ligue_one": "soccer/fra.1",
    "soccer_spain_segunda_division": "soccer/esp.2",
    "soccer_netherlands_eredivisie": "soccer/ned.1",
    "soccer_portugal_primeira_liga": "soccer/por.1",
    "basketball_nba": "basketball/nba",
    "icehockey_nhl": "hockey/nhl",
}
_espn_cache = {}


def _norm(nombre):
    t = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return " ".join(w for w in t.split() if w not in ("fc", "cf", "ac", "as", "sc", "de", "the", "club"))


def _mismo_equipo(a, b):
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.8


def _espn_scoreboard(ruta, dia):
    clave = (ruta, dia)
    if clave not in _espn_cache:
        try:
            res = requests.get(f"https://site.api.espn.com/apis/site/v2/sports/{ruta}/scoreboard",
                               params={"dates": dia, "limit": 100}, timeout=20)
            _espn_cache[clave] = res.json().get("events", []) if res.status_code == 200 else []
        except (requests.RequestException, ValueError) as e:
            print(f"Error ESPN {ruta} {dia}: {e}")
            _espn_cache[clave] = []
    return _espn_cache[clave]


def _evaluar_espn(leg):
    """Evalúa la pata ('won', 'lost', 'void') según ESPN, o devuelve None si no la encuentra."""
    ruta = ESPN_LIGAS.get(leg["sport"])
    if not ruta or " vs " not in leg["partido"]:
        return None
    local, visit = leg["partido"].split(" vs ", 1)
    inicio = datetime.fromtimestamp(leg["commence"], timezone.utc)
    for delta in (0, 1, -1):
        dia = (inicio + timedelta(days=delta)).strftime("%Y%m%d")
        for ev in _espn_scoreboard(ruta, dia):
            comp = (ev.get("competitions") or [{}])[0]
            # solo tiempo reglamentario terminado (no prórroga ni penaltis, que The Odds API no cuenta)
            estado = ev.get("status", {}).get("type", {}).get("name")
            if estado not in ("STATUS_FULL_TIME", "STATUS_FINAL"):
                continue
            equipos = {c.get("homeAway"): c for c in comp.get("competitors", [])}
            if "home" not in equipos or "away" not in equipos:
                continue
            h, a = equipos["home"], equipos["away"]
            if _mismo_equipo(h["team"]["displayName"], local) and _mismo_equipo(a["team"]["displayName"], visit):
                try:
                    sh, sa = float(h["score"]), float(a["score"])
                except (KeyError, TypeError, ValueError):
                    return None
                return _evaluar_pata(leg, sh, sa, local, visit)
    return None


def _clave_pata(leg):
    return (leg["id"], leg.get("mercado") or "h2h", leg.get("punto"), leg["raw"])


def liquidar(apuestas, ahora):
    """Comprueba resultados de apuestas pendientes. Devuelve (apuestas cerradas ahora, créditos restantes)."""
    ts = ahora.timestamp()
    pendientes = [b for b in apuestas if b["estado"] == "pendiente"]

    # 1) ESPN primero (gratis)
    por_espn = {}
    for b in pendientes:
        for leg in b["patas"]:
            if leg["resultado"] is None and ts > leg["commence"] + ESPERA_RESULTADO_H * 3600:
                res_espn = _evaluar_espn(leg)
                if res_espn is not None:
                    por_espn[_clave_pata(leg)] = res_espn

    # 2) The Odds API solo para lo que ESPN no resolvió
    deportes = {leg["sport"] for b in pendientes for leg in b["patas"]
                if leg["resultado"] is None and ts > leg["commence"] + ESPERA_RESULTADO_H * 3600
                and _clave_pata(leg) not in por_espn}
    resultados, restantes = {}, None
    for dep in deportes:
        resultados[dep], rest = obtener_resultados(dep)
        restantes = rest or restantes

    cerradas = []
    for b in pendientes:
        for leg in b["patas"]:
            if leg["resultado"] is not None:
                continue
            res_leg = por_espn.get(_clave_pata(leg))
            if res_leg is None:
                m = _marcador_odds(resultados.get(leg["sport"], {}).get(leg["id"]))
                if m is not None:
                    res_leg = _evaluar_pata(leg, *m)
            if res_leg is not None:
                leg["resultado"] = res_leg
            elif ts > leg["commence"] + DIAS_ANULAR * 86400:
                leg["resultado"] = "void"       # no se pudo comprobar: se anula
        res = [leg["resultado"] for leg in b["patas"]]
        if "lost" in res:
            b["estado"], b["beneficio"] = "perdida", -b["stake"]
        elif None not in res:
            ganadas = [leg for leg in b["patas"] if leg["resultado"] == "won"]
            if ganadas:
                c = 1.0
                for leg in ganadas:
                    c *= leg["cuota"]
                b["estado"], b["beneficio"] = "ganada", round(b["stake"] * (c - 1.0), 2)
            else:
                b["estado"], b["beneficio"] = "anulada", 0.0
        else:
            continue
        cerradas.append(b)
    return cerradas, restantes


def fmt_cerrada(b):
    icono = {"ganada": "✅", "perdida": "❌", "anulada": "➖"}[b["estado"]]
    patas = " + ".join(f"{esc(l['partido'])} → <b>{esc(l['sel'])}</b>" for l in b["patas"])
    return (f"{icono} {patas} @{b['cuota']}\n"
            f"   Resultado: <b>{b['beneficio']:+.2f}€</b> (stake sugerido {b['stake']:.2f}€)")


def resumen_balance(apuestas):
    cerr = [b for b in apuestas if b["estado"] in ("ganada", "perdida")]
    pend = sum(1 for b in apuestas if b["estado"] == "pendiente")
    if not cerr:
        return f"📒 <i>Apuestas pendientes de resultado: {pend}</i>" if pend else ""
    apostado = sum(b["stake"] for b in cerr)
    beneficio = sum(b["beneficio"] for b in cerr)
    esperado = sum(b["stake"] * b["edge"] / 100.0 for b in cerr)
    ganadas = sum(1 for b in cerr if b["estado"] == "ganada")
    roi = beneficio / apostado * 100.0 if apostado else 0.0
    texto = (f"📒 <b>BALANCE DE SUGERENCIAS</b>\n"
             f"Cerradas: {len(cerr)} (ganadas {ganadas}) · Pendientes: {pend}\n"
             f"Apostado: {apostado:.2f}€ · Beneficio: <b>{beneficio:+.2f}€</b> (ROI {roi:+.1f}%)\n"
             f"Beneficio esperado según el modelo: {esperado:+.2f}€")
    clv = [b["cuota"] * b["patas"][0]["cierre_prob"] - 1.0 for b in cerr
           if b["tipo"] == "simple" and b["patas"][0]["cierre_prob"]]
    if clv:
        texto += f"\nCLV medio ({len(clv)} simples): {sum(clv) / len(clv) * 100:+.1f}%"
    if len(cerr) < 30:
        texto += "\n<i>Pocas apuestas aún: el resultado es ruido, no saques conclusiones.</i>"
    return texto


# ==========================================
# 5. ANÁLISIS
# ==========================================
def esc(t):
    return html.escape(str(t))


def nombre_sel(sel, punto=None):
    if punto is not None:
        return f"{'Más' if sel == 'Over' else 'Menos'} de {punto:g}"
    return "Empate" if sel == "Draw" else sel


def analizar():
    estado = cargar_estado()
    ahora = datetime.now(timezone.utc)
    limite = ahora + timedelta(hours=HORAS_VENTANA)
    simples, simples_otras, candidatas = [], [], []
    restantes = "Desconocido"
    n_analizados, mejor_global, mejor_otra = 0, None, None   # para el resumen opcional

    apuestas = cargar_json(APUESTAS_PATH, [])
    cerradas, restantes_scores = liquidar(apuestas, ahora)
    if restantes_scores is not None:
        restantes = restantes_scores

    n_llamadas, presupuesto = 0, None
    max_ligas = MAX_LIGAS_EJECUCION * max(1, len(ODDS_API_KEYS))
    for dep in seleccionar_deportes(ahora, limite):
        if restantes != "Desconocido" and float(restantes) < CREDITOS_MINIMOS:
            if _rotar_clave():
                restantes, presupuesto = "Desconocido", None
            else:
                print("Quedan pocos créditos en todas las claves; se detiene.")
                break
        if presupuesto is not None and n_llamadas >= presupuesto:
            print(f"Presupuesto de {presupuesto} ligas por ejecución alcanzado.")
            break
        partidos, headers = obtener_cuotas(dep["key"])
        n_llamadas += 1
        restantes = headers.get("x-requests-remaining", restantes)
        if presupuesto is None and restantes != "Desconocido":
            # reparte los créditos (sumando las claves que aún quedan en cola) entre las ejecuciones restantes
            claves_en_cola = max(0, len(ODDS_API_KEYS) - _key_idx - 1)
            creditos_est = float(restantes) + n_llamadas * 2 + claves_en_cola * 500
            ejecuciones = dias_hasta_renovacion() * EJECUCIONES_DIA
            coste_por_liga = 2 if "totals" in MERCADOS else 1
            presupuesto = max(1, min(max_ligas, int(creditos_est * RESERVA_FACTOR / (ejecuciones * coste_por_liga))))
        if not partidos:
            continue

        for p in sorted(partidos, key=lambda x: x["commence_time"]):
            dt = datetime.fromisoformat(p["commence_time"].replace("Z", "+00:00"))
            if not (ahora < dt <= limite):
                continue
            casas = p.get("bookmakers", [])
            justas = prob_justa(casas)
            if not justas:
                continue
            cuotas_w = mejores_cuotas_winamax(casas)
            cuotas_otras = mejores_cuotas_otras_espana(casas)
            actualizar_cierre(apuestas, p["id"], justas, dep["key"])

            fecha = dt.astimezone(ZONA).strftime("%d/%m %H:%M")
            partido = f"{p['home_team']} vs {p['away_team']}"
            mejor_pata = None
            contado = False

            # 1) Analizar Winamax (con interpolación de puntos en baloncesto si las líneas difieren)
            for m_key, punto in cuotas_w:
                prob_l = prob_linea_para_casa(justas, (m_key, punto), dep["key"])
                if not prob_l or set(cuotas_w[(m_key, punto)]) != set(prob_l):
                    continue
                if not contado:
                    n_analizados += 1
                    contado = True
                for sel, cuota in cuotas_w[(m_key, punto)].items():
                    pr = prob_l[sel]
                    edge = (pr * cuota - 1.0) * 100.0
                    etiqueta = nombre_sel(sel, punto)
                    if edge > EDGE_DESCARTE:
                        print(f"Descartado por ventaja irreal (+{edge:.0f}%): {partido} {etiqueta} @{cuota}, "
                              f"cuota justa @{1 / pr:.2f}")
                        continue
                    if mejor_global is None or edge > mejor_global[0]:
                        mejor_global = (edge, partido, etiqueta, cuota)
                    datos = {"id": p["id"], "partido": partido, "liga": dep["nombre"], "fecha": fecha,
                             "sel": etiqueta, "cuota": cuota, "prob": pr, "edge": edge,
                             "raw": sel, "mercado": m_key, "punto": punto, "casa": "Winamax",
                             "sport": dep["key"], "commence": dt.timestamp(),
                             "local": p["home_team"], "visitante": p["away_team"]}
                    if CUOTA_MIN <= cuota <= CUOTA_MAX and edge >= UMBRAL_SIMPLE:
                        simples.append(datos)
                    if CUOTA_MIN <= cuota <= CUOTA_MAX_PATA and edge >= EDGE_MIN_PATA:
                        if not mejor_pata or edge > mejor_pata["edge"]:
                            mejor_pata = datos
            if mejor_pata:
                candidatas.append(mejor_pata)

            # 2) Analizar el resto de casas españolas (gratis en la misma consulta)
            for m_key, punto in cuotas_otras:
                prob_l = prob_linea_para_casa(justas, (m_key, punto), dep["key"])
                if not prob_l:
                    continue
                for sel, (cuota, nom_casa) in cuotas_otras[(m_key, punto)].items():
                    if sel not in prob_l or not (CUOTA_MIN <= cuota <= CUOTA_MAX):
                        continue
                    pr = prob_l[sel]
                    edge = (pr * cuota - 1.0) * 100.0
                    if edge > EDGE_DESCARTE:
                        continue
                    etiqueta = nombre_sel(sel, punto)
                    if mejor_otra is None or edge > mejor_otra[0]:
                        mejor_otra = (edge, nom_casa, partido, etiqueta, cuota)
                    if edge >= UMBRAL_OTRAS:
                        simples_otras.append({
                            "id": p["id"], "partido": partido, "liga": dep["nombre"], "fecha": fecha,
                            "sel": etiqueta, "cuota": cuota, "prob": pr, "edge": edge,
                            "raw": sel, "mercado": m_key, "punto": punto, "casa": nom_casa,
                            "sport": dep["key"], "commence": dt.timestamp(),
                            "local": p["home_team"], "visitante": p["away_team"],
                        })

    # --- Simples ---
    simples.sort(key=lambda x: x["edge"], reverse=True)
    bloques_simples = []
    for s in simples:
        if not es_nuevo(estado, f"S|{s['id']}|{s['sel']}|{s['cuota']}"):
            continue
        apuestas.append(nueva_apuesta("simple", s["cuota"], s["prob"], s["edge"],
                                      kelly_stake(s["prob"], s["cuota"]), [s]))
        aviso = "\n⚠️ <i>Ventaja muy alta: comprueba que la cuota sigue disponible.</i>" if s["edge"] > EDGE_SOSPECHOSO else ""
        bloques_simples.append(
            f"🎯 <b>WINAMAX +{s['edge']:.1f}%</b> · {esc(s['liga'])} · ⏰ {s['fecha']}h\n"
            f"⚔️ <b>{esc(s['partido'])}</b>\n"
            f"👉 <b>{esc(s['sel'])}</b> a cuota <b>@{s['cuota']}</b> "
            f"(cuota justa @{1 / s['prob']:.2f})\n"
            f"💶 Stake sugerido: <b>{kelly_stake(s['prob'], s['cuota']):.2f}€</b>{aviso}"
            + bloque_noticias([s["local"], s["visitante"]], NOTICIAS_POR_EQUIPO))

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
        apuestas.append(nueva_apuesta("doble", cuota, pr, edge,
                                      kelly_stake(pr, cuota, KELLY_FRACCION * COMBI_FACTOR), [a, b]))
        bloques_combis.append(
            f"🔗 <b>DOBLE WINAMAX +{edge:.1f}%</b>\n"
            f"1️⃣ {esc(a['partido'])} ({a['fecha']}h) 👉 <b>{esc(a['sel'])}</b> @{a['cuota']}\n"
            f"2️⃣ {esc(b['partido'])} ({b['fecha']}h) 👉 <b>{esc(b['sel'])}</b> @{b['cuota']}\n"
            f"💰 Cuota total <b>@{cuota}</b> · prob. real {pr * 100:.1f}%\n"
            f"💶 Stake sugerido: <b>{kelly_stake(pr, cuota, KELLY_FRACCION * COMBI_FACTOR):.2f}€</b>"
            + bloque_noticias([a["local"], a["visitante"], b["local"], b["visitante"]], 1))

    # --- Otras casas españolas ---
    simples_otras.sort(key=lambda x: x["edge"], reverse=True)
    bloques_otras = []
    for s in simples_otras:
        if len(bloques_otras) >= 5:
            break
        if not es_nuevo(estado, f"O|{s['casa']}|{s['id']}|{s['sel']}|{s['cuota']}"):
            continue
        apuestas.append(nueva_apuesta("simple", s["cuota"], s["prob"], s["edge"],
                                      kelly_stake(s["prob"], s["cuota"]), [s], casa=s["casa"]))
        bloques_otras.append(
            f"🏛 <b>{esc(s['casa'].upper())} +{s['edge']:.1f}%</b> · {esc(s['liga'])} · ⏰ {s['fecha']}h\n"
            f"⚔️ <b>{esc(s['partido'])}</b>\n"
            f"👉 <b>{esc(s['sel'])}</b> a cuota <b>@{s['cuota']}</b> "
            f"(cuota justa @{1 / s['prob']:.2f})\n"
            f"💶 Stake sugerido: <b>{kelly_stake(s['prob'], s['cuota']):.2f}€</b>"
            + bloque_noticias([s["local"], s["visitante"]], 1))

    guardar_estado(estado)
    guardar_json(APUESTAS_PATH, apuestas)

    bloques_cerradas = [fmt_cerrada(b) for b in cerradas]
    balance = resumen_balance(apuestas)
    info_clave = f" (clave {_key_idx + 1}/{len(ODDS_API_KEYS)})" if len(ODDS_API_KEYS) > 1 else ""

    if not (bloques_simples or bloques_combis or bloques_otras or bloques_cerradas):
        if ENVIAR_RESUMEN:
            texto = ("🤖 <b>RADAR WINAMAX · PRUEBA</b>\n\n"
                     "✅ Telegram funciona. No hay apuestas con valor ahora mismo.\n"
                     f"📊 Partidos analizados con Winamax: <b>{n_analizados}</b>")
            if mejor_global:
                e_, part, sel, cuota = mejor_global
                texto += (f"\n🔎 Más cercana en Winamax: <b>{esc(sel)}</b> en {esc(part)} "
                          f"@{cuota} ({e_:+.1f}%; umbral +{UMBRAL_SIMPLE}%)")
            if mejor_otra:
                e_, casa_, part, sel, cuota = mejor_otra
                texto += (f"\n🏛 Más cercana en otras casas ({esc(casa_)}): <b>{esc(sel)}</b> en {esc(part)} "
                          f"@{cuota} ({e_:+.1f}%; umbral +{UMBRAL_OTRAS}%)")
            if balance:
                texto += "\n\n" + balance
            texto += f"\n\n🔋 <i>Créditos restantes: {restantes}{info_clave}</i>"
            enviar_telegram(texto)
        else:
            print("Sin novedades; no se envía mensaje.")
        return

    partes = ["🤖 <b>RADAR WINAMAX + ESPAÑA</b>"]
    if bloques_cerradas:
        partes.append("🏁 <b>RESULTADOS DE TUS SUGERENCIAS</b>")
        partes += bloques_cerradas
    if bloques_simples:
        partes.append("🚨 <b>APUESTAS SIMPLES EN WINAMAX</b>")
        partes += bloques_simples[:5]
    if bloques_combis:
        partes.append("🔥 <b>COMBINADAS DOBLES EN WINAMAX</b>\n"
                      "<i>Mismas casa y cuota en las dos patas; más riesgo que una simple.</i>")
        partes += bloques_combis
    if bloques_otras:
        partes.append("👀 <b>VALOR EN OTRAS CASAS DE ESPAÑA</b>")
        partes += bloques_otras
    if balance:
        partes.append(balance)
    partes.append(f"🔋 <i>Créditos restantes: {restantes}{info_clave}</i>")
    enviar_telegram("\n\n".join(partes))


if __name__ == "__main__":
    if not (ODDS_API_KEY and TELEGRAM_TOKEN and CHAT_ID):
        raise SystemExit("Faltan los secrets ODDS_API_KEY, TELEGRAM_TOKEN y/o CHAT_ID.")
    analizar()
