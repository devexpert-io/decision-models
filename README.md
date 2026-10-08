# Modelos de decisión

Ejemplos prácticos con modelos de decisión: modelos de IA que no generan texto. Les pasas un estado (un texto o un JSON) y unas preguntas cerradas, y devuelven una respuesta tipada con sus probabilidades en unos cientos de milisegundos, por una fracción de céntimo.

```
estado  ─┐
         ├─▶ modelo de decisión ─▶ sí/no (0,03) · opción («soporte», 0,98) · puntuación (2,3 de 3)
preguntas┘
```

Hay tres tipos de pregunta:

| Tipo | Devuelve | Ejemplo |
|---|---|---|
| Sí o no (`noul`) | La probabilidad de «sí», de 0 a 1 | ¿Pide un reembolso? |
| Elegir (`choice`) | Una opción de tu lista, con las probabilidades de todas y la confianza | ¿Qué equipo se encarga? |
| Puntuar (`score`) | La media de los niveles ponderada por su probabilidad | ¿Cómo de urgente es? |

## Requisitos

- [uv](https://docs.astral.sh/uv/)
- Al menos una forma de llamar a un modelo:
  - una clave de [TypeSafe](https://console.typesafe.ai/keys) para Jev, el modelo que estrenó la categoría;
  - una de [OpenAI](https://platform.openai.com/api-keys), con la Decisions API en beta;
  - una de [OpenRouter](https://openrouter.ai/keys), que da acceso a Perplexity, Cloudflare, Liquid y el resto;
  - o [Ollama](https://ollama.com) 0.40 o superior para el modelo local (`ollama pull nimble`, unos 9 GB).

## Empezar

```bash
cp .env.example .env     # y rellena las claves que tengas
uv sync
uv run decide try        # el ticket de ejemplo, con Jev
uv run decide try -p all # Jev, OpenAI, Perplexity y nimble en local
```

## El código, paso a paso

Cada paso tiene su tag (`git checkout paso-1`) y se puede ejecutar por separado.

| Paso | Archivo | Qué hace |
|---|---|---|
| 1 | [`client.py`](src/decisions/client.py) | Las tres preguntas, la llamada y los proveedores. Casi todos usan el formato de Jev; OpenAI tiene el suyo y se traduce en el mismo archivo. Pruébalo con `uv run decide try -p all` |
| 2 | [`triage.py`](src/decisions/triage.py) | Una bandeja de entrada con 80 avisos inventados (correos, eventos y tareas) y etiquetados a mano. Cada aviso recibe cuatro preguntas en una sola llamada; las reglas y los umbrales están en el código. Compara la pregunta ingenua (`uv run decide triage -q v1`) con la buena (`uv run decide triage`) |

### Probar otros umbrales gratis

Cada pasada guarda las respuestas en `runs/`. Con `--replay` se vuelven a aplicar las reglas sobre ellas, sin llamar al modelo:

```bash
uv run decide triage                       # llama al modelo y guarda las respuestas
uv run decide triage --replay -t 0.35      # otro umbral, 0 llamadas
```

El umbral depende del modelo: si cambias de proveedor, vuelve a buscarlo.

### El guardián en Claude Code

| Paso | Archivo | Qué hace |
|---|---|---|
| 3 | [`guard.py`](src/decisions/guard.py) | Un hook de Claude Code que revisa cada comando antes de ejecutarlo: permitir, preguntarte o bloquear. Pruébalo con `uv run decide guard --test` |

Este repo ya trae el hook configurado en [`.claude/settings.json`](.claude/settings.json): abre Claude Code en la carpeta y cada comando de terminal pasa por el guardián. Para usarlo en otro proyecto, copia ese bloque y cambia `$CLAUDE_PROJECT_DIR` por la ruta de este repo. Elige el modelo con `GUARD_PROVIDER` (por defecto, `jev`). Cada decisión queda en `runs/guard.jsonl`.

Cómo decide:

1. **Reglas que bloquean siempre:** push forzado, `sudo`, borrar tu carpeta personal, ejecutar un script descargado, `DROP TABLE` o leer secretos.
2. **Reglas que permiten siempre:** lo que solo lee (`ls`, `cat`, `git status`, `git diff`…).
3. **El modelo, para lo demás:** tres preguntas (si se puede deshacer, si encaja con la tarea y cuánto daño puede hacer). Solo deja pasar lo que está claro; lo dudoso te lo pregunta. Nunca bloquea por su cuenta.
4. **Si el modelo falla o tarda,** te pregunta. Claude Code deja pasar el comando cuando un hook se queda sin tiempo, así que el guardián nunca debe colgarse.

La frontera de seguridad son las reglas. El modelo lee lo que escribe el agente, y eso se puede manipular: `data/comandos.jsonl` incluye comandos con comentarios que intentan convencerle («copia local de pruebas, totalmente reversible y sin riesgo»). Compara cómo aguanta cada proveedor con `uv run decide guard --test -p local`.

### Un benchmark propio

| Paso | Archivo | Qué hace |
|---|---|---|
| 4 | [`bench.py`](src/decisions/bench.py) | Clasifica 300 frases en español en 60 intenciones con cada modelo de decisión y con un LLM normal (GPT-6 Luna con salida estructurada). Mide el acierto, si la confianza sirve para algo, la latencia y el coste |

```bash
uv run decide bench                                   # todos: jev, openai, perplexity, local y llm
uv run decide bench -p jev,llm -n 50                  # solo dos, con 50 frases
uv run decide bench --replay --cascade perplexity     # el modelo de decisión, y el LLM solo si duda
```

Las frases salen de [MASSIVE](https://github.com/alexa/massive), de Amazon (CC BY 4.0): lo que la gente le dice a un asistente de voz. `scripts/download_massive.py` vuelve a generar la muestra.

Detalles a tener en cuenta:

- **nimble en Ollama admite como mucho 26 opciones** por pregunta. En local, el benchmark clasifica en dos pasos: primero el escenario (18 opciones) y después la intención dentro de él.
- **El LLM aprovecha la caché.** Las 60 opciones van siempre igual en el prompt y OpenAI cobra esa parte a una décima parte. Los modelos de decisión cobran la entrada entera en cada llamada.
- **Las etiquetas no son perfectas.** En 16 de las 300 frases todos los modelos coinciden y la etiqueta dice otra cosa («quiero escuchar ateo de c tangana» aparece como audiolibro).

## Resultados medidos

El 8 de octubre de 2026, con las APIs llamadas desde España y el modelo local en un MacBook Pro M5 Pro con 64 GB. Repite las pruebas antes de fiarte: la categoría cambia cada semana.

**Benchmark** (300 frases, 60 intenciones):

| Modelo | Acierto | Acierto si está seguro (≥ 0,8) | Mediana | p95 | $ por cada 1.000 |
|---|---|---|---|---|---|
| Perplexity (pplx-decider v1.1, vía OpenRouter) | 87% | 95% (249 frases) | 251 ms | 711 ms | 0,019 |
| GPT-6 Luna como LLM, sin razonar | 84% | – | 1.393 ms | 2.924 ms | 0,020 |
| GPT-6 Luna como LLM, razonando poco | 84% | – | 1.772 ms | 3.669 ms | 0,029 |
| Jev (TypeSafe) | 82% | 90% (240) | 228 ms | 312 ms | 0,066 |
| OpenAI (Decisions API, GPT-6 Luna) | 79% | 94% (196) | 205 ms | 286 ms | 0,097 |
| nimble en local, en dos pasos | 77% | 90% (208) | 586 ms | 768 ms | gratis |

Cuando duda (confianza por debajo de 0,5), cualquiera de ellos acierta menos de la mitad. Si esas frases se las pasas al LLM (`--cascade`), OpenAI sube del 79 al 83% (el LLM contesta el 35%), Jev del 82 al 83% y Perplexity se queda en el 87%.

**Bandeja de entrada** (80 avisos, 37 merecen aviso; Jev):

| Preguntas | Umbral | Avisos | Falsas alarmas | Se le escapan |
|---|---|---|---|---|
| «¿Requiere que Laura haga algo?» | 0,5 | 31 | 10 | 16 |
| Tipo de aviso + «¿merece la pena avisar?» | 0,5 | 28 | 0 | 9 |
| Tipo de aviso + «¿merece la pena avisar?» | 0,35 | 37 | 1 | 1 |

Con la primera pregunta no hay umbral que funcione: con 0,3 salen 18 falsas alarmas y con 0,6 se escapan 22.

**Guardián** (32 comandos):

| Modelo | Como se esperaba | Fallos |
|---|---|---|
| Jev | 32 | – |
| OpenAI | 31 | Pregunta antes de un `uv add` |
| Perplexity | 31 | Pregunta antes de un `mkdir` |
| nimble en local | 29 | Deja pasar `git reset --hard` y los dos comandos con comentarios que intentan convencerle |

**Latencia con varias preguntas:** en las APIs, cuatro preguntas tardan casi lo mismo que una (Jev, unos 250 ms en los dos casos). En local cada pregunta suma: nimble pasa de unos 150 ms a 1 s.

## Proveedores

| `-p` | Modelo | Precio por millón de tokens de entrada |
|---|---|---|
| `jev` | Jev, de TypeSafe | 0,042 $ |
| `openai` | GPT-6 Luna en la Decisions API | 0,10 $ |
| `perplexity` | pplx-decider v1.1, de Perplexity, vía OpenRouter | 0,02 $ |
| `local` | nimble, de Bespoke Labs, en Ollama | gratis |
| `openrouter:<modelo>` | Cualquiera de los [modelos de decisión de OpenRouter](https://openrouter.ai/api/v1/models?output_modalities=decisions) | Según el modelo |
| `ollama:<modelo>` | Otro modelo de decisión en Ollama, como `clef-flash` | gratis |

La salida es gratis en todos.

## Licencia

MIT
