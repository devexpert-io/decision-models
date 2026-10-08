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
