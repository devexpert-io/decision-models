"""Paso 3 · Un guardián para tu agente de código.

Claude Code ejecuta este script antes de cada comando de terminal (un hook
PreToolUse) y espera una de tres respuestas: permitir, preguntarte o bloquear.

1. Primero, las reglas fijas: lo que nunca se permite y lo que siempre se permite.
2. Lo que queda en medio va al modelo de decisión, que responde en unos 300 ms.
   El modelo solo puede dejar pasar lo inofensivo o subirte lo dudoso.
   Bloquear lo decide siempre una regla.

La frontera de seguridad son las reglas, no el modelo: el texto que lee el modelo
lo escribe el agente, y se puede manipular.
"""

import json
import re
import time
from pathlib import Path

from .client import decide, score, yes_no

# Nunca: da igual lo que diga el comando o el agente
ALWAYS_DENY = {
    r"\brm\s+-\w*[rf]\w*\s+(/|~|\$HOME)(\s|$)": "borrar la raíz o tu carpeta personal",
    r"\bgit\s+push\b.*(--force\b|-f\b|--force-with-lease)": "push forzado",
    r"\b(curl|wget)\b[^|]*\|\s*(ba|z)?sh\b": "ejecutar un script descargado",
    r"\b(DROP\s+(TABLE|DATABASE)|TRUNCATE\s+TABLE|DELETE\s+FROM\s+\w+\s*;?\s*(\"|'|$))": "borrar datos de una base de datos",
    r"\bchmod\s+-R\s+777\b": "dar permisos a todo el mundo",
    r"\bsudo\b": "sudo",
    r"\b(mkfs|dd\s+if=)": "escribir directamente en un disco",
    r"(\.ssh/|id_rsa|\.aws/credentials|(^|[\s/])\.env(\s|$))": "leer secretos",
}

# Siempre: solo leen
ALWAYS_ALLOW = [
    r"(ls|pwd|whoami|date)(\s+[^;&|>]*)?",
    r"(cat|head|tail|wc|file)\s+[^;&|>]+",
    r"(rg|grep)\s+[^;&|>]+",
    r"git\s+(status|diff|log|show|branch)(\s+[^;&|>]*)?",
]

QUESTIONS = {
    "reversible": yes_no(
        "¿Se puede deshacer fácilmente lo que hace este comando si sale mal?",
        yes="Solo lee, o cambia archivos que se recuperan con git o volviendo a ejecutar algo",
        no="Borra, sobrescribe, publica, envía o cobra algo que no se puede recuperar",
    ),
    "encaja": yes_no(
        "¿Es un paso normal para un agente de programación que trabaja en esta tarea?",
        yes="Leer, editar, probar, instalar dependencias o guardar en git cosas relacionadas con la tarea",
        no="Hace algo que la tarea no necesita, o actúa fuera del proyecto",
    ),
    "riesgo": score(
        "¿Cuánto daño puede hacer este comando si sale mal?",
        [
            "Ninguno",
            "Un poco de trabajo perdido",
            "Datos o trabajo importantes perdidos",
            "Daño a producción, a otras personas o a algo irrecuperable",
        ],
    ),
}

LOG = Path("runs/guard.jsonl")


def check(command: str, task: str, provider: str = "jev") -> dict:
    """Devuelve la decisión (allow, ask o deny), quién la ha tomado y por qué."""
    for pattern, why in ALWAYS_DENY.items():
        if re.search(pattern, command, re.IGNORECASE):
            return {"decision": "deny", "by": "regla", "reason": f"Bloqueado por una regla: {why}"}
    if any(re.fullmatch(pattern, command.strip()) for pattern in ALWAYS_ALLOW):
        return {"decision": "allow", "by": "regla", "reason": "Solo lectura"}

    try:
        d = decide({"tarea": task, "comando": command}, QUESTIONS, provider=provider, timeout=5)
    except Exception as error:  # si el modelo no contesta, decide la persona
        return {"decision": "ask", "by": "fallo", "reason": f"El guardián no ha podido decidir: {error}"}

    reversible = d.answers["reversible"].value
    fits = d.answers["encaja"].value
    risk = d.answers["riesgo"].value
    scores = f"reversible {reversible:.2f} · encaja {fits:.2f} · riesgo {risk:.1f} de 3"

    # Solo deja pasar lo que está claro; todo lo demás te lo pregunta
    if reversible >= 0.7 and fits >= 0.5 and risk < 1.5:
        decision = "allow"
    else:
        decision = "ask"
    return {"decision": decision, "by": "modelo", "reason": scores, "latency_ms": d.latency_ms}


def last_user_message(transcript_path: str | None) -> str:
    """La tarea: el último mensaje que escribió el usuario en la sesión de Claude Code."""
    if not transcript_path or not Path(transcript_path).exists():
        return "(desconocida)"
    task = "(desconocida)"
    for line in Path(transcript_path).read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message") or {}
        content = message.get("content")
        # Los mensajes que escribe la persona son texto; los resultados de herramientas, listas
        if entry.get("type") == "user" and isinstance(content, str) and not content.startswith("<"):
            task = content
    return task[:1000]


def hook(stdin: str, provider: str = "jev") -> str:
    """Lo que ejecuta Claude Code: JSON por la entrada, JSON por la salida."""
    event = json.loads(stdin)
    if event.get("tool_name") != "Bash":
        return ""  # sin respuesta: Claude Code sigue con sus permisos normales
    command = event["tool_input"]["command"]
    task = last_user_message(event.get("transcript_path"))

    result = check(command, task, provider)
    LOG.parent.mkdir(exist_ok=True)
    with LOG.open("a") as log:
        entry = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "command": command, "task": task, **result}
        log.write(json.dumps(entry, ensure_ascii=False) + "\n")

    return json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": result["decision"],
                "permissionDecisionReason": f"Guardián ({result['by']}): {result['reason']}",
            }
        },
        ensure_ascii=False,
    )
