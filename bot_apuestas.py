import requests
from datetime import datetime, timezone, timedelta
from itertools import combinations

# ==========================================
# 1. TUS CLAVES DE CONFIGURACIÓN
# ==========================================
ODDS_API_KEY = "5132116bf9df0f4636b614c029c7a690"
TELEGRAM_TOKEN = "8688601863:AAEVBvC8kRQ7fk_gFVBfya38ZogrhGVOw4w"
CHAT_ID = "799317842"

# Parámetros para Apuestas Simples
UMBRAL_SIMPLE = 1.5         # % mínimo de ventaja matemática para avisar en simple
CUOTA_MIN = 1.25            # Cuota mínima a considerar
CUOTA_MAX = 5.00            # Tope máximo (evitar cuotas imposibles)

# Parámetros para Combinadas Dobles en Marathonbet
CUOTA_MAX_COMBI = 2.85      # Solo juntar cuotas moderadas/favoritos para acertar a menudo
UMBRAL_SELECCION_COMBI = -0.5  # Aceptar cuotas a precio justo o con valor en Marathonbet

CASAS_ESPANA = [
    "marathon",
    "winamax",
    "william hill",
    "betfair",
    "888sport",
    "betsson",
    "casumo",
    "bwin",
    "sportium",
    "codere"
]


def obtener_deportes_hoy():
    """Elige las 4 mejores ligas según el día de la semana para ahorrar créditos."""
    dia_semana = datetime.now().weekday()  # 0=Lunes ... 4=Viernes, 5=Sábado, 6=Domingo
    if dia_semana in [1, 2, 3]:
        # Martes, Miércoles y Jueves: Champions, Euroliga, NBA y LaLiga
        return [
            {"key": "soccer_uefa_champs_league", "nombre": "🏆 Champions League"},
            {"key": "basketball_euroleague", "nombre": "🏀 Euroliga"},
            {"key": "basketball_nba", "nombre": "🏀 NBA"},
            {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga (España)"}
        ]
    else:
        # Viernes, Sábado, Domingo y Lunes: Grandes ligas de fútbol + Baloncesto
        return [
            {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga (España)"},
            {"key": "soccer_epl", "nombre": "⚽ Premier League"},
            {"key": "soccer_italy_serie_a", "nombre": "⚽ Serie A (Italia)"},
            {"key": "basketball_euroleague", "nombre": "🏀 Euroliga"}
        ]


def es_casa_espanola(key_casa, titulo_casa):
    texto = f"{key_casa} {titulo_casa}".lower()
    if "unibet" in texto and ("nl" in texto or "uk" in texto or "se" in texto):
        return False
    return any(casa in texto for casa in CASAS_ESPANA)


def enviar_telegram(mensaje):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    datos = {
        "chat_id": CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        res = requests.post(url, json=datos, timeout=15)
        if res.status_code != 200:
            print(f"Error Telegram: {res.text}")
    except Exception as e:
        print(f"Error de conexión: {e}")


def analizar_mercado():
    deportes_hoy = obtener_deportes_hoy()
    simples_marathon = []
    simples_otras = []
    candidatas_combi = []
    peticiones_restantes = "Desconocido"
    ahora_utc = datetime.now(timezone.utc)

    for deporte in deportes_hoy:
        clave_dep = deporte["key"]
        nombre_dep = deporte["nombre"]
        url = f"https://api.the-odds-api.com/v4/sports/{clave_dep}/odds/"
        parametros = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",
            "markets": "h2h",
            "oddsFormat": "decimal"
        }

        try:
            res = requests.get(url, params=parametros, timeout=20)
            restantes = res.headers.get("x-requests-remaining")
            if restantes:
                peticiones_restantes = restantes

            if res.status_code != 200:
                print(f"Aviso en {nombre_dep}: {res.text}")
                continue

            partidos = res.json()
        except Exception as e:
            print(f"Error en {nombre_dep}: {e}")
            continue

        for partido in partidos[:10]:
            local = partido.get("home_team", "Local")
            visitante = partido.get("away_team", "Visitante")
            id_partido = f"{local} vs {visitante}"
            fecha_raw = partido.get("commence_time", "")

            try:
                fecha_limpia = fecha_raw.replace("Z", "+00:00")
                dt_utc = datetime.fromisoformat(fecha_limpia)
                # Descartar partidos que ya han empezado
                if dt_utc <= ahora_utc:
                    continue
                # Convertir a hora española (+2 horas)
                dt_espana = dt_utc + timedelta(hours=2)
                fecha_str = dt_espana.strftime("%d/%m %H:%M")
            except Exception:
                fecha_str = "Próximamente"

            casas = partido.get("bookmakers", [])

            # 1. Buscar PINNACLE como verdad matemática
            pinnacle = next((c for c in casas if c["key"] == "pinnacle"), None)
            if not pinnacle or not pinnacle.get("markets"):
                continue

            outcomes_pin = pinnacle["markets"][0]["outcomes"]
            cuotas_pin = {o["name"]: o["price"] for o in outcomes_pin}

            suma_inv = sum(1.0 / p for p in cuotas_pin.values())
            prob_real = {n: (1.0 / p) / suma_inv for n, p in cuotas_pin.items()}

            # 2. Analizar MARATHONBET directamente (para Simples y para Combinadas)
            marathon = next(
                (c for c in casas if "marathon" in c["key"].lower() or "marathon" in c["title"].lower()),
                None
            )
            if marathon and marathon.get("markets"):
                mejor_op_combi = None
                for out in marathon["markets"][0]["outcomes"]:
                    sel = out["name"]
                    cuota_m = out["price"]
                    if sel not in prob_real:
                        continue

                    prob = prob_real[sel]
                    ventaja_m = (prob * cuota_m - 1.0) * 100.0
                    sel_es = "Empate" if sel == "Draw" else sel
                    c_justa = round(1.0 / prob, 2)

                    # A) Guardar si es un Chollo Simple en Marathonbet
                    if CUOTA_MIN <= cuota_m <= CUOTA_MAX and ventaja_m >= UMBRAL_SIMPLE:
                        texto_m = (
                            f"🎯 <b>MARATHONBET (+{ventaja_m:.1f}% de ventaja)</b>\n"
                            f"🏆 {nombre_dep} | ⏰ {fecha_str}h\n"
                            f"⚔️ <b>{id_partido}</b>\n"
                            f"👉 Entra a: <b>{sel_es}</b> a cuota <b>@{cuota_m}</b>\n"
                            f"📊 <i>(Precio matemático real: @{c_justa})</i>"
                        )
                        simples_marathon.append((ventaja_m, texto_m))

                    # B) Guardar candidata si sirve para armar Combinada en Marathonbet
                    if CUOTA_MIN <= cuota_m <= CUOTA_MAX_COMBI and ventaja_m >= UMBRAL_SELECCION_COMBI:
                        datos_op = {
                            "partido": id_partido,
                            "deporte": nombre_dep,
                            "fecha": fecha_str,
                            "seleccion": sel_es,
                            "cuota": cuota_m,
                            "prob": prob,
                            "ventaja": ventaja_m
                        }
                        if not mejor_op_combi or ventaja_m > mejor_op_combi["ventaja"]:
                            mejor_op_combi = datos_op

                if mejor_op_combi:
                    candidatas_combi.append(mejor_op_combi)

            # 3. Revisar otras casas españolas por si hay un desajuste grande (>2.5%)
            for casa in casas:
                if casa["key"] == "pinnacle" or not casa.get("markets"):
                    continue
                if "marathon" in casa["key"].lower():
                    continue
                if not es_casa_espanola(casa["key"], casa["title"]):
                    continue

                nom_casa = casa["title"]
                for out in casa["markets"][0]["outcomes"]:
                    sel = out["name"]
                    cuota_c = out["price"]
                    if not (CUOTA_MIN <= cuota_c <= CUOTA_MAX):
                        continue
                    if sel in prob_real:
                        ventaja = (prob_real[sel] * cuota_c - 1.0) * 100.0
                        if ventaja >= 2.5:
                            sel_es = "Empate" if sel == "Draw" else sel
                            linea_otra = (
                                f"• <b>{id_partido}</b> ({fecha_str}h)\n"
                                f"  👉 <b>{sel_es}</b> en {nom_casa} <b>@{cuota_c}</b> (+{ventaja:.1f}%)"
                            )
                            simples_otras.append((ventaja, linea_otra))

    # ==========================================
    # 4. GENERADOR DE COMBINADAS DOBLES EN MARATHONBET
    # ==========================================
    candidatas_combi.sort(key=lambda x: x["ventaja"], reverse=True)
    top_candidatas = candidatas_combi[:8]

    mejores_combis = []
    for op1, op2 in combinations(top_candidatas, 2):
        if op1["partido"] == op2["partido"]:
            continue

        cuota_total = round(op1["cuota"] * op2["cuota"], 2)
        prob_conjunta = op1["prob"] * op2["prob"]
        ventaja_combi = (prob_conjunta * cuota_total - 1.0) * 100.0

        # Solo recomendar si la combinada total suma ventaja positiva
        if ventaja_combi >= 1.0 and cuota_total <= 7.00:
            prob_porcentaje = round(prob_conjunta * 100, 1)
            texto_combi = (
                f"🔗 <b>CUPÓN DOBLE MARATHONBET (+{ventaja_combi:.1f}% Valor)</b>\n"
                f"1️⃣ {op1['partido']} ({op1['fecha']}h) 👉 <b>{op1['seleccion']}</b> (@{op1['cuota']})\n"
                f"2️⃣ {op2['partido']} ({op2['fecha']}h) 👉 <b>{op2['seleccion']}</b> (@{op2['cuota']})\n"
                f"💰 <b>Cuota Total en Marathonbet: @{cuota_total}</b> (Prob. real: {prob_porcentaje}%)"
            )
            mejores_combis.append((ventaja_combi, texto_combi))

    # Ordenar todas las listas de mayor a menor ventaja matemática
    simples_marathon.sort(key=lambda x: x[0], reverse=True)
    mejores_combis.sort(key=lambda x: x[0], reverse=True)
    simples_otras.sort(key=lambda x: x[0], reverse=True)

    # ==========================================
    # 5. CONSTRUIR EL MENSAJE PARA TELEGRAM
    # ==========================================
    mensaje = "🤖 <b>RADAR 3.0: ESPECIAL MARATHONBET</b>\n\n"

    # Bloque 1: Simples en Marathonbet
    if simples_marathon:
        mensaje += "🚨 <b>APUESTAS SIMPLES EN MARATHONBET:</b>\n\n"
        mensaje += "\n\n".join([x[1] for x in simples_marathon[:4]]) + "\n\n"
    else:
        mensaje += "✅ <i>Sin simples en Marathonbet por encima del +1.5% ahora mismo.</i>\n\n"

    # Bloque 2: Combinadas Dobles en Marathonbet
    if mejores_combis:
        mensaje += "🔥 <b>COMBINADAS DOBLES RECOMENDADAS (MARATHONBET):</b>\n\n"
        mensaje += "\n\n".join([x[1] for x in mejores_combis[:3]]) + "\n\n"
    else:
        mensaje += "ℹ️ <i>No hay suficientes favoritos con ventaja para armar combinada ahora.</i>\n\n"

    # Bloque 3: Otros chollos en España
    if simples_otras:
        mensaje += "👀 <b>OTROS DESAJUSTES EN ESPAÑA (+2.5%):</b>\n"
        mensaje += "\n".join([x[1] for x in simples_otras[:4]]) + "\n\n"

    mensaje += f"🔋 <i>Consultas gratis restantes: {peticiones_restantes}/500</i>"
    enviar_telegram(mensaje)


if __name__ == "__main__":
    analizar_mercado()
