"""Línea de comandos: `decide try`."""

import argparse

import httpx
from rich.console import Console
from rich.table import Table

from .client import PROVIDERS, DecisionError, choice, decide, score, yes_no

console = Console()

TICKET = (
    "Hola, desde ayer no puedo entrar en la plataforma del curso. Me dice «contraseña "
    "incorrecta» aunque la he cambiado dos veces. Tengo la entrega del proyecto final "
    "mañana a las 9."
)

QUESTIONS = {
    "equipo": choice(
        "¿Qué equipo debe encargarse de este ticket?",
        {
            "soporte": "Acceso a la cuenta, contraseñas y uso de la plataforma",
            "facturacion": "Pagos, facturas y devoluciones",
            "contenido": "Dudas sobre el material del curso",
        },
    ),
    "urgencia": score(
        "¿Cómo de urgente es?",
        ["Puede esperar semanas", "Esta semana", "En las próximas 24 horas", "Ahora mismo"],
    ),
    "reembolso": yes_no("¿El cliente pide que le devolvamos el dinero?"),
}


def describe(answer) -> str:
    if answer.type == "noul":
        return f"{answer.value:.2f}"
    if answer.type == "refusal":
        return "no contesta"
    value = answer.value if answer.type == "choice" else f"{answer.value:.2f}"
    return f"{value} ({answer.confidence:.2f})"


def cmd_try(args: argparse.Namespace) -> None:
    """La misma pregunta a uno o a todos los proveedores."""
    state = {"ticket": args.text}
    names = list(PROVIDERS) if args.provider == "all" else [args.provider]

    table = Table(title=f"«{args.text[:60]}…»" if len(args.text) > 60 else f"«{args.text}»")
    table.add_column("Proveedor")
    for name in QUESTIONS:
        table.add_column(name)
    table.add_column("ms", justify="right")
    table.add_column("$", justify="right")

    for name in names:
        with httpx.Client() as http:
            try:
                # La primera llamada no cuenta: abre la conexión y, en local, carga el modelo.
                # Va con otro texto para que la buena no salga de ninguna caché.
                decide({"ticket": "Hola"}, {"saludo": yes_no("¿Es un saludo?")}, provider=name, http=http)
                d = decide(state, QUESTIONS, provider=name, http=http)
            except DecisionError as error:
                console.print(f"[red]{error}[/red]")
                continue
        cells = [describe(d.answers[q]) for q in QUESTIONS]
        cost = f"{d.cost:.6f}" if d.cost is not None else "-"
        table.add_row(d.provider, *cells, f"{d.latency_ms:.0f}", cost)
        if args.verbose:
            console.print_json(data=d.raw)

    console.print(table)
    console.print("Entre paréntesis, la confianza. Sí o no: la probabilidad de «sí».")


def main() -> None:
    parser = argparse.ArgumentParser(prog="decide")
    commands = parser.add_subparsers(dest="command", required=True)

    try_cmd = commands.add_parser("try", help="clasifica un ticket de soporte")
    try_cmd.add_argument("text", nargs="?", default=TICKET)
    try_cmd.add_argument("--provider", "-p", default="jev", help="jev, openai, perplexity, local o all")
    try_cmd.add_argument("--verbose", "-v", action="store_true", help="muestra la respuesta completa")
    try_cmd.set_defaults(func=cmd_try)

    args = parser.parse_args()
    args.func(args)
