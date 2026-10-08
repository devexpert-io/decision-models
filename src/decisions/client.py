"""Paso 1 · Una decisión, en cualquier proveedor.

Un modelo de decisión no genera texto. Le pasas un estado (texto o JSON) y unas
preguntas cerradas, y te devuelve una respuesta tipada con sus probabilidades.

Casi todos los proveedores usan el formato que estrenó Jev, de TypeSafe: `state` y
`questions`, con tres tipos de pregunta (`noul` para sí o no, `choice` y `score`).
OpenAI es la excepción: misma idea, otro formato. Lo traducimos más abajo.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

OPENROUTER_URL = "https://openrouter.ai/api/alpha/decisions"
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


# --- Los tres tipos de pregunta ---------------------------------------------------


def yes_no(instructions: str, yes: str | None = None, no: str | None = None) -> dict:
    """Sí o no: devuelve la probabilidad de «sí», de 0 a 1. Jev lo llama `noul`."""
    question: dict[str, Any] = {"type": "noul", "instructions": instructions}
    if yes or no:
        question["criteria"] = {"true": yes, "false": no}
    return question


def choice(instructions: str, options: dict[str, str | None]) -> dict:
    """Elegir una opción de una lista cerrada (hasta 255): nunca se inventa otra."""
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: str, levels: list[str]) -> dict:
    """Puntuar en una escala ordenada (de 2 a 10 niveles), de menos a más."""
    return {"type": "score", "instructions": instructions, "criteria": levels}


# --- Proveedores --------------------------------------------------------------------


@dataclass(frozen=True)
class Provider:
    label: str
    url: str
    model: str
    key_env: tuple[str, ...] = ()
    price_per_m: float = 0.0  # $ por millón de tokens de entrada; la salida es gratis
    openai_format: bool = False


PROVIDERS = {
    "jev": Provider(
        "Jev (TypeSafe)",
        "https://api.typesafe.ai/v1/systemone",
        "jev-latest",
        ("TYPESAFE_API_KEY", "JEV_API_KEY"),
        0.042,
    ),
    "openai": Provider(
        "OpenAI",
        "https://api.openai.com/v1/decisions",
        "gpt-6-luna",
        ("OPENAI_API_KEY",),
        0.10,
        openai_format=True,
    ),
    "perplexity": Provider(
        "Perplexity (OpenRouter)",
        OPENROUTER_URL,
        "perplexity/pplx-decider-v1.1-27b",
        ("OPENROUTER_API_KEY",),
        0.02,
    ),
    "local": Provider("nimble (Ollama, en local)", f"{OLLAMA_URL}/v1/systemone", "nimble"),
}


def get_provider(name: str) -> Provider:
    """`jev`, `openai`, `perplexity`, `local`, `openrouter:<modelo>` u `ollama:<modelo>`."""
    if name in PROVIDERS:
        return PROVIDERS[name]
    if name.startswith("openrouter:"):
        model = name.split(":", 1)[1]
        return Provider(f"{model} (OpenRouter)", OPENROUTER_URL, model, ("OPENROUTER_API_KEY",))
    if name.startswith("ollama:"):
        model = name.split(":", 1)[1]
        return Provider(f"{model} (Ollama, en local)", f"{OLLAMA_URL}/v1/systemone", model)
    raise ValueError(f"Proveedor desconocido: {name}. Usa uno de {', '.join(PROVIDERS)}")


# --- Respuestas ---------------------------------------------------------------------


@dataclass
class Answer:
    type: str  # "noul", "choice" o "score" (o "refusal" en OpenAI)
    value: Any  # probabilidad de sí, opción elegida o puntuación
    probabilities: dict[str, float]
    confidence: float | None = None


@dataclass
class Decision:
    provider: str
    model: str
    answers: dict[str, Answer]
    latency_ms: float
    input_tokens: int | None
    cost: float | None
    raw: dict


class DecisionError(RuntimeError):
    pass


def decide(
    state: str | dict | list,
    questions: dict[str, dict],
    provider: str = "jev",
    http: httpx.Client | None = None,
    timeout: float = 30,
) -> Decision:
    """Una llamada: el estado y las preguntas entran, las respuestas tipadas salen."""
    p = get_provider(provider)
    if p.openai_format:
        body = to_openai(p.model, state, questions)
    else:
        body = {"model": p.model, "state": state, "questions": questions}

    headers = {"content-type": "application/json"}
    key = next((os.environ[k] for k in p.key_env if os.environ.get(k)), None)
    if p.key_env and not key:
        raise DecisionError(f"Falta la clave de {p.label}: define {p.key_env[0]} en .env")
    if key:
        headers["authorization"] = f"Bearer {key}"

    start = time.perf_counter()
    try:
        response = (http or httpx).post(p.url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as error:
        raise DecisionError(f"{p.label}: {error}") from error
    latency_ms = (time.perf_counter() - start) * 1000

    if response.status_code != 200:
        raise DecisionError(f"{p.label}: HTTP {response.status_code} {response.text[:300]}")
    data = response.json()
    answers = parse_openai(data) if p.openai_format else parse_jev(data)

    usage = data.get("usage") or {}
    tokens = usage.get("input_tokens")
    # OpenRouter devuelve el coste; con el resto lo calculamos con el precio de entrada
    cost = usage.get("cost")
    if cost is None and tokens is not None:
        cost = tokens * p.price_per_m / 1_000_000
    return Decision(p.label, data.get("model", p.model), answers, latency_ms, tokens, cost, data)


def parse_jev(data: dict) -> dict[str, Answer]:
    answers = {}
    for name, a in data["answers"].items():
        if a["type"] == "noul":
            p = a["noul"]
            answers[name] = Answer("noul", p, {"true": p, "false": 1 - p})
        else:  # choice y score traen probabilidades y confianza
            value = a["choice"] if a["type"] == "choice" else a["score"]
            answers[name] = Answer(a["type"], value, a["probabilities"], a.get("confidence"))
    return answers


# --- El formato de OpenAI -----------------------------------------------------------
#
# `input` en vez de `state`, una lista de preguntas con `name`, `predicate` en vez de
# `noul`, y las opciones y los niveles como listas de objetos.


def to_openai(model: str, state: str | dict | list, questions: dict[str, dict]) -> dict:
    items = []
    for name, q in questions.items():
        item: dict[str, Any] = {"name": name, "instructions": q["instructions"]}
        if q["type"] == "noul":
            item["type"] = "predicate"
            criteria = q.get("criteria")
            if criteria:  # OpenAI no tiene criterios para sí o no: van en las instrucciones
                item["instructions"] += f"\nYes: {criteria['true']}\nNo: {criteria['false']}"
        elif q["type"] == "choice":
            item["type"] = "choice"
            item["choices"] = [
                {"value": value, "description": description or value}
                for value, description in q["criteria"].items()
            ]
        else:
            item["type"] = "score"
            item["levels"] = [
                {"label": str(i), "description": level} for i, level in enumerate(q["criteria"])
            ]
        items.append(item)
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    return {"model": model, "input": text, "questions": items}


def parse_openai(data: dict) -> dict[str, Answer]:
    answers = {}
    for a in data["answers"]:
        name = a["name"]
        if a["type"] == "predicate":
            p = a["probability"]
            answers[name] = Answer("noul", p, {"true": p, "false": 1 - p})
        elif a["type"] == "choice":
            probs = {x["value"]: x["probability"] for x in a["probabilities"]}
            answers[name] = Answer("choice", a["choice"], probs, a.get("confidence"))
        elif a["type"] == "score":
            probs = {str(x["value"]): x["probability"] for x in a["probabilities"]}
            answers[name] = Answer("score", a["score"], probs, a.get("confidence"))
        else:  # "refusal": OpenAI puede negarse a contestar
            answers[name] = Answer("refusal", None, {})
    return answers
