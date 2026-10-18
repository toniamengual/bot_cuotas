import requests
from datetime import datetime

# ==========================================
# 1. TUS CLAVES DE CONFIGURACIÓN (YA LISTAS)
# ==========================================
ODDS_API_KEY = "5132116bf9df0f4636b614c029c7a690"
TELEGRAM_TOKEN = "8688601863:AAEVBvC8kRQ7fk_gFVBfya38ZogrhGVOw4w"
CHAT_ID = "799317842"

# Ligas que vamos a escanear (LaLiga, Premier League y Euroliga de Baloncesto)
DEPORTES = [
    {"key": "soccer_spain_la_liga", "nombre": "⚽ LaLiga (España)"},
    {"key": "soccer_epl", "nombre": "⚽ Premier League (Inglaterra)"},
    {"key": "basketball_euroleague", "nombre": "🏀 Euroliga (Baloncesto)"}
]

# Porcentaje mínimo de ventaja matemática para que salte alerta (ej. 2.5%)
UMBRAL_VALOR_MINIMO = 2.5


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
    """Escanea las cuotas en tiempo real y busca valor matemático."""
    alertas_valor = []
    referencias_bet365 = []
    peticiones_restantes = "Desconocido"

    for deporte in DEPORTES:
        url = f"https://api.the-odds-api.com/v4/sports/{deporte['key']}/odds/"
        parametros = {
            "apiKey": ODDS_API_KEY,
            "regions": "eu",        # Casas de apuestas europeas (incluye Pinnacle, Winamax, William Hill...)
            "markets": "h2h",       # Ganador del partido (1X2 en fútbol, 1-2 en baloncesto)
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

        # Analizar hasta los próximos 8 partidos de cada liga
        for partido in partidos[:8]:
            local = partido.get("home_team")
            visitante = partido.get("away_team")
            fecha_iso = partido.get("commence_time", "")
            
            try:
                dt = datetime.fromisoformat(fecha_iso.replace("Z", "+00:00"))
                fecha_str = dt.strftime("%d/%m %H:%M")
            except Exception:
                fecha_str = "Próximamente"

            casas = partido.get("bookmakers", [])
            
            # 1. Buscar la cuota profesional de PINNACLE (nuestra referencia matemática)
            pinnacle = next((c for c in casas if c["key"] == "pinnacle"), None)
            if not pinnacle or not pinnacle.get("markets"):
                continue

            cuotas_pin = {o["name"]: o["price"] for o in pinnacle["markets"][0]["outcomes"]}
            
            # Quitar el margen de ganancia de Pinnacle para obtener la Probabilidad Real Pura
            suma_inversas = sum(1.0 / precio for precio in cuotas_pin.values())
            prob_real = {nombre: (1.0 / precio) / suma_inversas for nombre, precio in cuotas_pin.items()}

            # Calcular cuotas objetivo para Bet365 (para ganar un +3% de ventaja matemática)
            cuotas_min_bet365 = {nombre: round(1.03 / prob, 2) for nombre, prob in prob_real.items()}
            
            texto_b365 = f"<b>{local} vs {visitante}</b> ({fecha_str})\n"
            for opcion, cuota_min in cuotas_min_bet365.items():
                opcion_es = "Empate" if opcion == "Draw" else opcion
                texto_b365 += f"   • {opcion_es}: entra en Bet365 solo si paga <b>@{cuota_min}</b> o más\n"
            referencias_bet365.append(texto_b365)

            # 2. Comparar contra el resto de casas europeas para cazar fallos de precio
            for casa in casas:
                if casa["key"] == "pinnacle" or not casa.get("markets"):
                    continue
                
                nombre_casa = casa["title"]
                for outcome in casa["markets"][0]["outcomes"]:
                    seleccion = outcome["name"]
                    cuota_casa = outcome["price"]
                    
                    if seleccion in prob_real:
                        # Fórmula de Valor Esperado: (Probabilidad_Real * Cuota_Casa - 1) * 100
                        ventaja_matematica = (prob_real[seleccion] * cuota_casa - 1.0) * 100.0
                        
                        if ventaja_matematica >= UMBRAL_VALOR_MINIMO:
                            seleccion_es = "Empate" if seleccion == "Draw" else seleccion
                            cuota_justa = round(1.0 / prob_real[seleccion], 2)
                            alerta = (
                                f"🔥 <b>¡VALOR DETECTADO! (+{ventaja_matematica:.1f}%)</b>\n"
                                f"🏆 {deporte['nombre']}\n"
                                f"⚔️ <b>{local} vs {visitante}</b> ({fecha_str})\n"
                                f"🎯 Apuesta a: <b>{seleccion_es}</b>\n"
                                f"🏛 Casa detectada: <b>{nombre_casa}</b> a cuota <b>@{cuota_casa}</b>\n"
                                f"📊 Cuota real justa: @{cuota_justa}\n"
                                f"💡 <i>Mira en Bet365 o en {nombre_casa}: si se paga a @{cuota_casa} o cerca, tiene ventaja matemática.</i>"
                            )
                            alertas_valor.append(alerta)

    # ==========================================
    # 3. CONSTRUIR Y ENVIAR EL MENSAJE A TELEGRAM
    # ==========================================
    cabecera = "🤖 <b>RADAR MATEMÁTICO DE CUOTAS</b>\n\n"

    if alertas_valor:
        cuerpo_alertas = "🚨 <b>OPORTUNIDADES CAZADAS AHORA MISMO:</b>\n\n" + "\n\n".join(alertas_valor[:6])
        enviar_telegram(cabecera + cuerpo_alertas)
    else:
        sin_alertas = (
            "✅ Escaneo completado. Ahora mismo ninguna casa supera el "
            f"+{UMBRAL_VALOR_MINIMO}% de desfase directo.\n\n"
        )
        guia_b365 = (
            "📌 <b>GUÍA DE PRECIOS PARA TU BET365 HOY:</b>\n"
            "<i>(Solo apuesta si en tu app de Bet365 ves una cuota igual o mayor a esta):</i>\n\n"
            + "\n".join(referencias_bet365[:6])
        )
        pie = f"\n🔋 <i>Consultas gratis restantes este mes: {peticiones_restantes}/500</i>"
        enviar_telegram(cabecera + sin_alertas + guia_b365 + pie)


if __name__ == "__main__":
    analizar_ligas()
