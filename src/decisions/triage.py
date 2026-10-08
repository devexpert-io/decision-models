"""Paso 2 · Una bandeja de entrada con umbrales.

Cada aviso nuevo (un correo, un evento o una tarea) pasa por el modelo de decisión
con varias preguntas a la vez, en una sola llamada. El modelo puntúa, pero quien
decide es el código, con reglas y umbrales.

Cada respuesta se guarda en `runs/`, así que puedes probar otros umbrales sin
volver a llamar al modelo.
"""

import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .client import choice, decide, score, yes_no

# La primera versión: la pregunta que parece obvia
QUESTIONS_V1 = {
    "requiere_accion": yes_no("¿Este aviso requiere que Laura haga algo?"),
}

# La versión buena: primero qué es, después si merece la pena interrumpirla
QUESTIONS_V2 = {
    "tipo": choice(
        "¿Qué tipo de aviso es?",
        {
            "verificacion": "Un código de verificación o de doble factor, un aviso de inicio de sesión, un cambio de contraseña o un aviso de seguridad de la cuenta",
            "automatico": "Una notificación automática, un recibo, una newsletter, publicidad o un mensaje de un sistema",
            "peticion": "Una persona (alumno, cliente, colaborador, proveedor, familia) que escribe a Laura y espera una respuesta, una decisión, un documento o que asista a algo",
            "oportunidad": "Una oportunidad de negocio: un cliente potencial, una formación, un patrocinio, una charla o una colaboración",
            "dinero": "Un problema con dinero: un cobro fallido, una suscripción cancelada, una factura vencida o una devolución",
            "plazo": "Un plazo o un requisito administrativo, fiscal, legal o de una plataforma",
            "propio": "Una tarea, una nota o un bloque de tiempo que Laura o su asistente han escrito para ella misma",
            "reunion": "Una reunión, una llamada o un evento con otras personas",
            "otro": "Nada de lo anterior",
        },
    ),
    "merece_avisar": yes_no(
        "¿Debería un asistente personal avisar a Laura de esto ahora? Laura da cursos de "
        "programación online. Ya ve lo que provoca ella misma (códigos de verificación, "
        "inicios de sesión que acaba de hacer, sus notas y sus bloques de tiempo) y las "
        "notificaciones rutinarias.",
        yes="Es nuevo para ella y hay algo que ella o el asistente deberían hacer, contestar o decidir",
        no="Lo ha provocado ella, es rutinario o solo informa, o es una nota suya",
    ),
    "siguiente_paso": choice(
        "¿Quién puede dar el siguiente paso?",
        {
            "asistente": "Un asistente con acceso al correo, el calendario, los archivos y la web puede hacerlo o prepararlo: redactar una respuesta, buscar información o reunir documentos",
            "laura": "Solo Laura puede hacerlo: una llamada, una gestión en persona, firmar, pagar o una decisión personal",
            "nadie": "No hace falta ningún paso: es informativo, rutinario o ya está resuelto",
        },
    ),
    "urgencia": score(
        "¿Cuándo hay que atenderlo?",
        ["Sin plazo o dentro de varias semanas", "En las próximas dos semanas", "Esta semana", "Hoy o mañana"],
    ),
}

QUESTIONS = {"v1": QUESTIONS_V1, "v2": QUESTIONS_V2}

# Las reglas viven en el código, no en el prompt
NEVER = {"verificacion", "propio"}  # lo provoca ella misma: nunca se avisa


def act(version: str, answers: dict, threshold: float) -> str:
    """Convierte las probabilidades en una acción: avisar, preparar o ignorar."""
    if version == "v1":
        return "avisar" if answers["requiere_accion"]["value"] >= threshold else "ignorar"

    if answers["tipo"]["value"] in NEVER:
        return "ignorar"
    if answers["merece_avisar"]["value"] < threshold:
        return "ignorar"
    # Si el asistente puede adelantar trabajo, lo prepara antes de avisar
    if answers["siguiente_paso"]["probabilities"].get("asistente", 0) >= 0.6:
        return "preparar"
    return "avisar"


def load_signals(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def as_state(signal: dict) -> dict:
    """Lo que ve el modelo: el aviso, sin las etiquetas que hemos puesto a mano."""
    return {k: signal[k] for k in ("fuente", "de", "asunto", "texto")}


def run(signals: list[dict], version: str, provider: str, log: Path) -> list[dict]:
    """Una llamada por aviso, con todas las preguntas, y en paralelo."""
    questions = QUESTIONS[version]
    # Ollama atiende las peticiones de una en una: en paralelo solo harían cola
    local = provider == "local" or provider.startswith("ollama:")

    with httpx.Client() as http, ThreadPoolExecutor(max_workers=1 if local else 8) as pool:

        def one(signal: dict) -> dict:
            d = decide(as_state(signal), questions, provider=provider, http=http)
            return {
                "id": signal["id"],
                "answers": {
                    name: {"value": a.value, "probabilities": a.probabilities, "confidence": a.confidence}
                    for name, a in d.answers.items()
                },
                "latency_ms": d.latency_ms,
                "cost": d.cost,
            }

        records = list(pool.map(one, signals))

    log.parent.mkdir(exist_ok=True)
    log.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
    return records


def replay(log: Path) -> list[dict]:
    """Lee las respuestas guardadas: probar otro umbral no cuesta nada."""
    return [json.loads(line) for line in log.read_text().splitlines() if line.strip()]


def evaluate(signals: list[dict], records: list[dict], version: str, threshold: float) -> dict:
    by_id = {s["id"]: s for s in signals}
    rows = []
    for r in records:
        signal = by_id[r["id"]]
        action = act(version, r["answers"], threshold)
        rows.append({"signal": signal, "action": action, "answers": r["answers"]})

    raised = [x for x in rows if x["action"] != "ignorar"]
    return {
        "rows": rows,
        "raised": len(raised),
        "hits": sum(x["signal"]["avisar"] for x in raised),
        "false_alarms": [x for x in raised if not x["signal"]["avisar"]],
        "missed": [x for x in rows if x["action"] == "ignorar" and x["signal"]["avisar"]],
        "latency_ms": statistics.median(r["latency_ms"] for r in records),
        "cost": sum(r["cost"] or 0 for r in records),
    }
