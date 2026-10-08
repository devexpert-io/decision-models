"""Paso 4 · Un benchmark propio: modelo de decisión frente a LLM.

300 frases en español de MASSIVE, el dataset de Amazon con lo que la gente le dice
a un asistente de voz. Hay que clasificar cada frase en una de 60 intenciones: una
pregunta `choice` con 60 opciones.

La misma tarea, con los modelos de decisión y con un LLM normal (GPT-6 Luna por la
API de siempre, con salida estructurada). Medimos el acierto, si la confianza sirve
para algo, la latencia y el coste.
"""

import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .client import choice, decide

INTENTS = {
    "alarm_query": "Preguntar por las alarmas que hay puestas",
    "alarm_remove": "Quitar o cancelar una alarma",
    "alarm_set": "Poner una alarma",
    "audio_volume_down": "Bajar el volumen",
    "audio_volume_mute": "Silenciar el altavoz",
    "audio_volume_other": "Otros ajustes de volumen, como ponerlo a un nivel concreto",
    "audio_volume_up": "Subir el volumen",
    "calendar_query": "Preguntar por eventos o citas del calendario",
    "calendar_remove": "Borrar o cancelar un evento del calendario",
    "calendar_set": "Crear un evento, una cita o un recordatorio en el calendario",
    "cooking_query": "Preguntas de cocina: tiempos, cantidades o cómo se hace algo",
    "cooking_recipe": "Pedir una receta",
    "datetime_convert": "Convertir horas entre zonas horarias o formatos",
    "datetime_query": "Preguntar la hora o la fecha",
    "email_addcontact": "Añadir un contacto o una dirección de correo",
    "email_query": "Consultar los correos recibidos",
    "email_querycontact": "Consultar los datos de un contacto",
    "email_sendemail": "Enviar o responder un correo",
    "general_greet": "Saludar",
    "general_joke": "Pedir un chiste",
    "general_quirky": "Charla o preguntas raras que no encajan en otra categoría",
    "iot_cleaning": "Encender o programar el robot aspirador",
    "iot_coffee": "Pedir que prepare un café",
    "iot_hue_lightchange": "Cambiar el color de las luces",
    "iot_hue_lightdim": "Bajar la intensidad de las luces",
    "iot_hue_lightoff": "Apagar las luces",
    "iot_hue_lighton": "Encender las luces",
    "iot_hue_lightup": "Subir la intensidad de las luces",
    "iot_wemo_off": "Apagar un enchufe inteligente",
    "iot_wemo_on": "Encender un enchufe inteligente",
    "lists_createoradd": "Crear una lista o añadir algo a una lista",
    "lists_query": "Consultar qué hay en una lista",
    "lists_remove": "Quitar algo de una lista o borrar una lista",
    "music_dislikeness": "Decir que no te gusta una canción",
    "music_likeness": "Decir que te gusta una canción o un artista",
    "music_query": "Preguntar qué canción suena u otros datos de música",
    "music_settings": "Ajustes de reproducción, como aleatorio o repetir",
    "news_query": "Pedir noticias",
    "play_audiobook": "Reproducir un audiolibro",
    "play_game": "Jugar a un juego",
    "play_music": "Reproducir música",
    "play_podcasts": "Reproducir un podcast",
    "play_radio": "Poner la radio",
    "qa_currency": "Preguntar por el cambio de divisas",
    "qa_definition": "Preguntar qué significa una palabra",
    "qa_factoid": "Preguntas de cultura general o datos concretos",
    "qa_maths": "Hacer una cuenta",
    "qa_stock": "Preguntar por el precio de una acción en bolsa",
    "recommendation_events": "Pedir recomendaciones de eventos o planes",
    "recommendation_locations": "Pedir recomendaciones de sitios, como restaurantes o tiendas",
    "recommendation_movies": "Pedir recomendaciones de películas",
    "social_post": "Publicar algo en redes sociales o quejarse a una empresa",
    "social_query": "Consultar novedades en redes sociales",
    "takeaway_order": "Pedir comida para llevar o a domicilio",
    "takeaway_query": "Preguntar por un pedido de comida o si un restaurante hace envíos",
    "transport_query": "Preguntar por horarios o información de transporte",
    "transport_taxi": "Pedir un taxi",
    "transport_ticket": "Comprar o reservar billetes de transporte",
    "transport_traffic": "Preguntar por el tráfico",
    "weather_query": "Preguntar por el tiempo",
}

INSTRUCTIONS = "¿Qué le pide la persona al asistente de voz?"
QUESTIONS = {"intencion": choice(INSTRUCTIONS, INTENTS)}

# Las intenciones se agrupan en 18 escenarios: el prefijo de su nombre
SCENARIOS = {
    "alarm": "Alarmas",
    "audio": "Volumen del altavoz",
    "calendar": "Calendario y citas",
    "cooking": "Cocina y recetas",
    "datetime": "Fecha y hora",
    "email": "Correo y contactos",
    "general": "Saludos, chistes y charla",
    "iot": "Casa inteligente: luces, enchufes, aspirador o cafetera",
    "lists": "Listas",
    "music": "Gustos y ajustes de música",
    "news": "Noticias",
    "play": "Reproducir música, radio, podcasts, audiolibros o juegos",
    "qa": "Preguntas de definiciones, cuentas, divisas, bolsa o cultura general",
    "recommendation": "Recomendaciones de planes, sitios o películas",
    "social": "Redes sociales",
    "takeaway": "Comida a domicilio",
    "transport": "Transporte: billetes, taxis, horarios o tráfico",
    "weather": "El tiempo",
}

# El LLM de referencia: el mismo GPT-6 Luna, por la API de Responses
LLM_URL = "https://api.openai.com/v1/responses"
LLM_MODEL = "gpt-6-luna"
LLM_PRICES = {"input": 0.10, "cached": 0.01, "output": 0.50}  # $ por millón de tokens


def classify(text: str, provider: str, http: httpx.Client) -> dict:
    """Con un modelo de decisión: una pregunta, 60 opciones."""
    d = decide({"frase": text}, QUESTIONS, provider=provider, http=http, timeout=60)
    answer = d.answers["intencion"]
    return {"predicted": answer.value, "confidence": answer.confidence, "latency_ms": d.latency_ms, "cost": d.cost}


def classify_in_two_steps(text: str, provider: str, http: httpx.Client) -> dict:
    """Para modelos que admiten pocas opciones (nimble en Ollama: 26 como mucho).

    Primero el escenario, con 18 opciones, y después la intención dentro de él.
    """
    state = {"frase": text}
    first = decide(state, {"escenario": choice(INSTRUCTIONS, SCENARIOS)}, provider=provider, http=http, timeout=60)
    scenario = first.answers["escenario"]
    options = {name: description for name, description in INTENTS.items() if name.split("_")[0] == scenario.value}
    result = {"latency_ms": first.latency_ms, "cost": first.cost or 0}
    if len(options) == 1:
        return {"predicted": next(iter(options)), "confidence": scenario.confidence, **result}

    second = decide(state, {"intencion": choice(INSTRUCTIONS, options)}, provider=provider, http=http, timeout=60)
    intent = second.answers["intencion"]
    return {
        "predicted": intent.value,
        "confidence": min(scenario.confidence, intent.confidence),  # tan seguro como el paso más dudoso
        "latency_ms": result["latency_ms"] + second.latency_ms,
        "cost": result["cost"] + (second.cost or 0),
    }


def classify_llm(text: str, http: httpx.Client, effort: str = "none") -> dict:
    """Con un LLM: las mismas opciones en el prompt y un JSON con la intención elegida."""
    options = "\n".join(f"- {name}: {description}" for name, description in INTENTS.items())
    schema = {
        "type": "object",
        "properties": {"intencion": {"type": "string", "enum": list(INTENTS)}},
        "required": ["intencion"],
        "additionalProperties": False,
    }
    body = {
        "model": LLM_MODEL,
        "reasoning": {"effort": effort},
        "instructions": f"{INSTRUCTIONS} Elige una de estas intenciones:\n{options}",
        "input": text,
        "text": {"format": {"type": "json_schema", "name": "intencion", "schema": schema, "strict": True}},
    }
    headers = {"authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"}
    start = time.perf_counter()
    response = http.post(LLM_URL, json=body, headers=headers, timeout=120)
    latency_ms = (time.perf_counter() - start) * 1000
    response.raise_for_status()
    data = response.json()

    message = next(item for item in data["output"] if item["type"] == "message")
    output = next(part["text"] for part in message["content"] if part["type"] == "output_text")
    usage = data["usage"]
    cached = (usage.get("input_tokens_details") or {}).get("cached_tokens", 0)
    cost = (
        (usage["input_tokens"] - cached) * LLM_PRICES["input"]
        + cached * LLM_PRICES["cached"]
        + usage["output_tokens"] * LLM_PRICES["output"]
    ) / 1_000_000
    return {"predicted": json.loads(output)["intencion"], "confidence": None, "latency_ms": latency_ms, "cost": cost}


def run(rows: list[dict], provider: str, log: Path, two_steps: bool = False) -> list[dict]:
    local = provider == "local" or provider.startswith("ollama:")
    with httpx.Client() as http, ThreadPoolExecutor(max_workers=1 if local else 8) as pool:

        def one(row: dict) -> dict:
            if provider.startswith("llm"):
                effort = provider.split(":", 1)[1] if ":" in provider else "none"
                result = classify_llm(row["texto"], http, effort)
            elif two_steps or local:
                result = classify_in_two_steps(row["texto"], provider, http)
            else:
                result = classify(row["texto"], provider, http)
            return {"id": row["id"], "expected": row["intencion"], **result}

        records = list(pool.map(one, rows))

    log.parent.mkdir(exist_ok=True)
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    return records


def cascade(first: list[dict], fallback: list[dict], threshold: float = 0.8) -> list[dict]:
    """El modelo de decisión contesta; si duda, pregunta al LLM.

    Se calcula con las respuestas ya guardadas de los dos, sin llamar a nadie.
    """
    by_id = {r["id"]: r for r in fallback}
    records = []
    for r in first:
        if r["confidence"] >= threshold:
            records.append({**r, "escalated": False})
            continue
        llm = by_id[r["id"]]
        records.append(
            {
                **r,
                "predicted": llm["predicted"],
                "latency_ms": r["latency_ms"] + llm["latency_ms"],
                "cost": (r["cost"] or 0) + (llm["cost"] or 0),
                "escalated": True,
            }
        )
    return records


def summarize(records: list[dict]) -> dict:
    hits = [r["predicted"] == r["expected"] for r in records]
    latencies = sorted(r["latency_ms"] for r in records)
    cost = sum(r["cost"] or 0 for r in records)
    summary = {
        "n": len(records),
        "accuracy": sum(hits) / len(records),
        "p50_ms": statistics.median(latencies),
        "p95_ms": latencies[int(0.95 * (len(latencies) - 1))],
        "cost": cost,
        "cost_per_1000": cost / len(records) * 1000,
    }
    if "escalated" in records[0]:
        summary["escalated"] = sum(r["escalated"] for r in records) / len(records)
    elif records[0]["confidence"] is not None:
        # ¿Sirve la confianza? Acierto cuando el modelo está seguro y cuando no
        for name, low, high in (("high", 0.8, 1.01), ("mid", 0.5, 0.8), ("low", 0.0, 0.5)):
            band = [hit for r, hit in zip(records, hits) if low <= r["confidence"] < high]
            summary[f"band_{name}"] = {"n": len(band), "accuracy": sum(band) / len(band) if band else None}
    return summary
