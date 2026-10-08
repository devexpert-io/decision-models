"""Línea de comandos: `decide try | triage | guard | bench`."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

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


def cmd_triage(args: argparse.Namespace) -> None:
    """Pasa los avisos de la bandeja por el modelo y aplica las reglas."""
    from .client import get_provider
    from .triage import evaluate, load_signals, replay, run

    signals = load_signals(Path(args.data))
    slug = args.provider.replace(":", "_").replace("/", "_")
    log = Path("runs") / f"triage-{args.questions}-{slug}.jsonl"

    if args.replay:
        records = replay(log)
        origin = "respuestas guardadas: 0 llamadas, 0 $"
    else:
        start = time.perf_counter()
        records = run(signals, args.questions, args.provider, log)
        origin = f"{len(records)} llamadas en {time.perf_counter() - start:.1f} s"

    r = evaluate(signals, records, args.questions, args.threshold)
    console.print(
        f"\nPreguntas [bold]{args.questions}[/bold] · {get_provider(args.provider).label} · "
        f"umbral {args.threshold} · {origin}"
    )

    table = Table()
    for column in ("Avisos", "Aciertos", "Falsas alarmas", "Se le escapan", "Mediana", "Coste"):
        table.add_column(column, justify="right")
    table.add_row(
        f"{r['raised']} de {len(signals)}",
        str(r["hits"]),
        f"[red]{len(r['false_alarms'])}[/red]",
        f"[yellow]{len(r['missed'])}[/yellow]",
        "-" if args.replay else f"{r['latency_ms']:.0f} ms",
        "0 $" if args.replay else f"{r['cost']:.4f} $",
    )
    console.print(table)

    for title, items, color in (("Falsas alarmas", r["false_alarms"], "red"), ("Se le escapan", r["missed"], "yellow")):
        if items:
            console.print(f"[{color}]{title}:[/{color}]")
            for x in items:
                console.print(f"  · {x['signal']['asunto']}  [dim]({x['signal']['tipo']})[/dim]")

    if args.detail:
        detail = Table("Asunto", *records[0]["answers"].keys(), "Acción")
        for x in r["rows"]:
            cells = []
            for a in x["answers"].values():
                value = a["value"]
                cells.append(value if isinstance(value, str) else f"{value:.2f}")
            detail.add_row(x["signal"]["asunto"][:48], *cells, x["action"])
        console.print(detail)


COLORS = {"allow": "green", "ask": "yellow", "deny": "red"}


def cmd_guard(args: argparse.Namespace) -> None:
    """Sin --test, es el hook: lee el evento de Claude Code y contesta con la decisión."""
    from .guard import check, hook

    provider = args.provider or os.environ.get("GUARD_PROVIDER", "jev")
    if not args.test:
        output = hook(sys.stdin.read(), provider)
        if output:
            print(output)
        return

    cases = [json.loads(line) for line in Path(args.data).read_text().splitlines() if line.strip()]
    table = Table("Comando", "Esperado", "Guardián", "Quién", "Motivo")
    matches = 0
    for case in cases:
        r = check(case["comando"], case["tarea"], provider)
        ok = r["decision"] == case["esperado"]
        matches += ok
        color = COLORS[r["decision"]]
        command = case["comando"] + (" [magenta](inyección)[/magenta]" if case["nota"] else "")
        reason = r["reason"] + (f" · {r['latency_ms']:.0f} ms" if "latency_ms" in r else "")
        table.add_row(
            command,
            case["esperado"],
            f"[{color}]{r['decision']}[/{color}]" + ("" if ok else " ✗"),
            r["by"],
            reason,
        )
    console.print(table)
    console.print(f"{matches} de {len(cases)} como esperábamos")


LLM_LABELS = {"llm": "GPT-6 Luna como LLM, sin razonar", "llm:low": "GPT-6 Luna como LLM, razonando poco"}


def cmd_bench(args: argparse.Namespace) -> None:
    """La misma clasificación con varios modelos: acierto, confianza, latencia y coste."""
    from .bench import cascade, run, summarize
    from .client import get_provider

    rows = [json.loads(line) for line in Path(args.data).read_text().splitlines() if line.strip()]
    rows = rows[: args.limit]

    def pct(x: float | None) -> str:
        return "-" if x is None else f"{x:.0%}"

    table = Table("Modelo", "Acierto", "Si está seguro", "Si duda", "Mediana", "p95", "$ por 1.000", "Total")
    results = {}
    for provider in args.providers.split(","):
        local = provider == "local" or provider.startswith("ollama:")
        two_steps = (args.two_steps or local) and not provider.startswith("llm")
        slug = provider.replace(":", "_").replace("/", "_") + ("-2pasos" if two_steps else "")
        # Una pasada parcial va a su propio archivo para no pisar la completa
        log = Path("runs") / f"bench-{slug}{f'-n{args.limit}' if args.limit else ''}.jsonl"
        if args.replay:
            records = [json.loads(line) for line in log.read_text().splitlines()]
            elapsed = None
        else:
            console.print(f"[dim]{provider}: {len(rows)} frases…[/dim]")
            start = time.perf_counter()
            records = run(rows, provider, log, two_steps)
            elapsed = time.perf_counter() - start

        s = summarize(records)
        s["elapsed_s"] = elapsed
        results[slug] = s
        label = LLM_LABELS.get(provider) or get_provider(provider).label
        if two_steps:
            label += ", en dos pasos"
        high, low = s.get("band_high"), s.get("band_low")
        table.add_row(
            label,
            pct(s["accuracy"]),
            f"{pct(high['accuracy'])} de {high['n']}" if high else "-",
            f"{pct(low['accuracy'])} de {low['n']}" if low else "-",
            f"{s['p50_ms']:.0f} ms",
            f"{s['p95_ms']:.0f} ms",
            f"{s['cost_per_1000']:.3f}",
            "-" if elapsed is None else f"{elapsed:.0f} s",
        )

    # La cascada sale de las respuestas guardadas: el modelo de decisión, y el LLM solo si duda
    for provider in filter(None, (args.cascade or "").split(",")):
        first = [json.loads(line) for line in Path(f"runs/bench-{provider}.jsonl").read_text().splitlines()]
        llm = [json.loads(line) for line in Path("runs/bench-llm.jsonl").read_text().splitlines()]
        s = summarize(cascade(first, llm))
        results[f"{provider}+llm"] = s
        table.add_row(
            f"{get_provider(provider).label} + LLM si duda",
            pct(s["accuracy"]),
            "-",
            f"LLM en el {pct(s['escalated'])}",
            f"{s['p50_ms']:.0f} ms",
            f"{s['p95_ms']:.0f} ms",
            f"{s['cost_per_1000']:.3f}",
            "-",
        )

    console.print(table)
    console.print("«Si está seguro»: confianza de 0,8 o más. «Si duda»: menos de 0,5.")
    if not args.replay and not args.limit:
        Path("runs/bench-summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(prog="decide")
    commands = parser.add_subparsers(dest="command", required=True)

    try_cmd = commands.add_parser("try", help="clasifica un ticket de soporte")
    try_cmd.add_argument("text", nargs="?", default=TICKET)
    try_cmd.add_argument("--provider", "-p", default="jev", help="jev, openai, perplexity, local o all")
    try_cmd.add_argument("--verbose", "-v", action="store_true", help="muestra la respuesta completa")
    try_cmd.set_defaults(func=cmd_try)

    triage_cmd = commands.add_parser("triage", help="decide qué avisos de la bandeja merecen tu atención")
    triage_cmd.add_argument("--questions", "-q", choices=["v1", "v2"], default="v2", help="versión de las preguntas")
    triage_cmd.add_argument("--provider", "-p", default="jev")
    triage_cmd.add_argument("--threshold", "-t", type=float, default=0.5, help="umbral para avisar")
    triage_cmd.add_argument("--replay", action="store_true", help="reutiliza las respuestas guardadas en runs/")
    triage_cmd.add_argument("--detail", "-d", action="store_true", help="muestra la respuesta a cada aviso")
    triage_cmd.add_argument("--data", default="data/correos.jsonl")
    triage_cmd.set_defaults(func=cmd_triage)

    guard_cmd = commands.add_parser("guard", help="hook de Claude Code que revisa cada comando")
    guard_cmd.add_argument("--test", action="store_true", help="prueba el guardián con data/comandos.jsonl")
    guard_cmd.add_argument("--provider", "-p", default=None, help="por defecto, GUARD_PROVIDER o jev")
    guard_cmd.add_argument("--data", default="data/comandos.jsonl")
    guard_cmd.set_defaults(func=cmd_guard)

    bench_cmd = commands.add_parser("bench", help="compara modelos clasificando 300 frases en 60 intenciones")
    bench_cmd.add_argument(
        "--providers", "-p", default="jev,openai,perplexity,local,llm", help="separados por comas; llm es GPT-6 Luna"
    )
    bench_cmd.add_argument("--limit", "-n", type=int, default=None, help="usa solo las primeras N frases")
    bench_cmd.add_argument(
        "--two-steps", action="store_true", help="escenario y luego intención (en local, siempre: admite 26 opciones)"
    )
    bench_cmd.add_argument("--replay", action="store_true", help="resume las respuestas guardadas en runs/")
    bench_cmd.add_argument(
        "--cascade", help="modelos de decisión que pasan al LLM cuando dudan, separados por comas (necesita --replay)"
    )
    bench_cmd.add_argument("--data", default="data/massive-es-300.jsonl")
    bench_cmd.set_defaults(func=cmd_bench)

    args = parser.parse_args()
    args.func(args)
