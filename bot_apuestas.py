import requests
from datetime import datetime

# ==========================================
# 1. TUS CLAVES DE CONFIGURACIÓN
# ==========================================
ODDS_API_KEY = "5132116bf9df0f4636b614c029c7a690"
TELEGRAM_TOKEN = "8688601863:AAEVBvC8kRQ7fk_gFVBfya38ZogrhGVOw4w"
CHAT_ID = "799317842"

DEPORTES = [
    {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga (España)"},
    {"key": "soccer_epl", "nombre": "⚽ Premier League (Inglaterra)"},
    {"key": "basketball_euroleague", "nombre": "🏀 Euroliga (Baloncesto)"}
]

UMBRAL_VALOR_MINIMO = 2.0
CUOTA_MINIMA = 1.25
CUOTA_MAXIMA = 5.00

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


def analizar_ligas():
    alertas_agrupadas = []
    referencias_bet365 = []
    peticiones_restantes = "Desconocido"

    for deporte in DEPORTES:
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

        for partido in partidos[:8]:
            local = partido.get("home_team", "Local")
            visitante = partido.get("away_team", "Visitante")
            fecha_raw = partido.get("commence_time", "")

            try:
                fecha_limpia = fecha_raw.replace("Z", "+00:00")
                dt = datetime.fromisoformat(fecha_limpia)
                fecha_str = dt.strftime("%d/%m %H:%M")
            except Exception:
                fecha_str = "Próximamente"

            casas = partido.get("bookmakers", [])

            pinnacle = next((c for c in casas if c["key"] == "pinnacle"), None)
            if not pinnacle or not pinnacle.get("markets"):
                continue

            outcomes_pin = pinnacle["markets"][0]["outcomes"]
            cuotas_pin = {o["name"]: o["price"] for o in outcomes_pin}

            suma_inv = sum(1.0 / p for p in cuotas_pin.values())
            prob_real = {n: (1.0 / p) / suma_inv for n, p in cuotas_pin.items()}

            lineas_b365 = []
            for opcion, prob in prob_real.items():
                cuota_obj = round(1.03 / prob, 2)
                if CUOTA_MINIMA <= cuota_obj <= CUOTA_MAXIMA:
                    op_es = "Empate" if opcion == "Draw" else opcion
                    linea = f"   • {op_es}: entra si Bet365 paga <b>@{cuota_obj}</b> o más"
                    lineas_b365.append(linea)

            if lineas_b365:
                cab_b365 = f"<b>{local} vs {visitante}</b> ({fecha_str})\n"
                referencias_bet365.append(cab_b365 + "\n".join(lineas_b365))

            oportunidades = {}
            for casa in casas:
                if casa["key"] == "pinnacle" or not casa.get("markets"):
                    continue
                if not es_casa_espanola(casa["key"], casa["title"]):
                    continue

                nom_casa = casa["title"]
                for out in casa["markets"][0]["outcomes"]:
                    sel = out["name"]
                    cuota_c = out["price"]

                    if not (CUOTA_MINIMA <= cuota_c <= CUOTA_MAXIMA):
                        continue

                    if sel in prob_real:
                        ventaja = (prob_real[sel] * cuota_c - 1.0) * 100.0
                        if ventaja >= UMBRAL_VALOR_MINIMO:
                            oportunidades.setdefault(sel, []).append((ventaja, nom_casa, cuota_c))

            for sel, lista_c in oportunidades.items():
                lista_c.sort(key=lambda x: x[0], reverse=True)
                mejor_v, _, _ = lista_c[0]

                sel_es = "Empate" if sel == "Draw" else sel
                c_justa = round(1.0 / prob_real[sel], 2)
                c_min_b365 = round(c_justa + 0.08, 2)

                resumen = ", ".join([f"<b>{c} @{q}</b> (+{v:.1f}%)" for v, c, q in lista_c[:3]])

                alerta = (
                    f"🔥 <b>¡VALOR EN ESPAÑA! (Hasta +{mejor_v:.1f}%)</b>\n"
                    f"🏆 {nombre_dep}\n"
                    f"⚔️ <b>{local} vs {visitante}</b> ({fecha_str})\n"
                    f"🎯 Apuesta a: <b>{sel_es}</b> (Cuota justa: @{c_justa})\n"
                    f"🏛 Casas: {resumen}\n"
                    f"💡 <i>En Bet365 entra si paga @{c_min_b365} o más.</i>"
                )
                alertas_agrupadas.append(alerta)

    mensaje = "🤖 <b>RADAR MATEMÁTICO 2.0 (Máx @5.00)</b>\n\n"

    if alertas_agrupadas:
        mensaje += "🚨 <b>CHOLLOS EN CASAS ESPAÑOLAS:</b>\n\n"
        mensaje += "\n\n".join(alertas_agrupadas[:5]) + "\n\n"
    else:
        mensaje += (
            "✅ <i>Ninguna casa española supera ahora el "
            f"+{UMBRAL_VALOR_MINIMO}% en cuotas menores de @{CUOTA_MAXIMA}.</i>\n\n"
        )

    if referencias_bet365:
        mensaje += (
            "📌 <b>GUÍA DE PRECIOS PARA TU BET365 HOY:</b>\n"
            "<i>(Entra en Bet365 SOLO si paga igual o más que esto):</i>\n\n"
            + "\n\n".join(referencias_bet365[:6])
        )

    mensaje += f"\n\n🔋 <i>Consultas gratis restantes: {peticiones_restantes}/500</i>"
    enviar_telegram(mensaje)


if __name__ == "__main__":
    analizar_ligas()
