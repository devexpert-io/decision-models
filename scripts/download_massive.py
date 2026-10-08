"""Descarga MASSIVE, de Amazon, y prepara la muestra en español del benchmark.

MASSIVE son frases que la gente le dice a un asistente de voz, etiquetadas con
60 intenciones («pon una alarma», «sube el volumen», «¿qué tiempo hace?»…).
Licencia CC BY 4.0: https://github.com/alexa/massive

    uv run scripts/download_massive.py

Escribe data/massive-es-300.jsonl: 5 frases por intención del conjunto de test
(menos si no hay tantas) y el resto al azar hasta 300. Siempre la misma muestra.
"""

import json
import random
import tarfile
import urllib.request
from collections import defaultdict
from pathlib import Path

URL = "https://amazon-massive-nlu-dataset.s3.amazonaws.com/amazon-massive-dataset-1.1.tar.gz"
RAW = Path("data/raw/massive-1.1.tar.gz")
OUT = Path("data/massive-es-300.jsonl")
SIZE = 300
PER_INTENT = 5


def main() -> None:
    if not RAW.exists():
        RAW.parent.mkdir(parents=True, exist_ok=True)
        print(f"Descargando {URL} (40 MB)…")
        urllib.request.urlretrieve(URL, RAW)

    with tarfile.open(RAW) as tar:
        lines = tar.extractfile("1.1/data/es-ES.jsonl").read().decode().splitlines()
    test = [row for row in map(json.loads, lines) if row["partition"] == "test"]

    rng = random.Random(7)
    by_intent = defaultdict(list)
    for row in test:
        by_intent[row["intent"]].append(row)

    sample = []
    for intent in sorted(by_intent):
        sample += rng.sample(by_intent[intent], min(PER_INTENT, len(by_intent[intent])))
    chosen = {row["id"] for row in sample}
    rest = [row for row in test if row["id"] not in chosen]
    sample += rng.sample(rest, SIZE - len(sample))
    rng.shuffle(sample)

    OUT.write_text(
        "".join(
            json.dumps({"id": row["id"], "texto": row["utt"], "intencion": row["intent"]}, ensure_ascii=False) + "\n"
            for row in sample
        )
    )
    print(f"{len(sample)} frases de {len(by_intent)} intenciones en {OUT}")


if __name__ == "__main__":
    main()
