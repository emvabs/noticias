"""Measure how well the word lists label hand-labelled headlines.

Usage:  python scripts/evaluate.py [--all] [pt|en]
        --all also lists the correct ones; a language argument tests only that one.
"""
import csv
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.sentiment import get_scorer  # noqa: E402

LABELS = ["positive", "neutral", "negative"]
PT = {"positive": "positiva", "neutral": "neutra", "negative": "negativa"}


FILES = {"pt": "headlines.csv", "en": "headlines_en.csv"}
NAMES = {"pt": "Português", "en": "Inglês"}


def evaluate(language, show_all):
    scorer = get_scorer(language)
    path = Path(__file__).resolve().parent.parent / "eval" / FILES[language]
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    print(f"\n=== {NAMES[language]} ({FILES[language]}) ===")
    confusion = Counter()
    correct = 0
    for r in rows:
        score, label, matched = scorer.score(r["text"])
        ok = label == r["label"]
        correct += ok
        confusion[(r["label"], label)] += 1
        if show_all or not ok:
            words = ", ".join(("¬" if m["negated"] else "") + m["word"] + ("+" if m["polarity"] > 0 else "−")
                              for m in matched) or "—"
            mark = "✓" if ok else "✗"
            print(f"{mark} esperado={PT[r['label']]:<8} obtido={PT[label]:<8} score={score:+.2f}  {r['text']}\n"
                  f"      palavras: {words}")
    n = len(rows)
    print(f"\nExatidão: {correct}/{n} = {correct / n:.0%}\n")
    print("Matriz de confusão (linhas = esperado, colunas = obtido)")
    print(" " * 10 + "".join(f"{PT[l]:>10}" for l in LABELS))
    for exp in LABELS:
        print(f"{PT[exp]:<10}" + "".join(f"{confusion[(exp, got)]:>10}" for got in LABELS))
    return correct, n


def main():
    show_all = "--all" in sys.argv
    languages = [a for a in sys.argv[1:] if a in FILES] or list(FILES)
    for language in languages:
        evaluate(language, show_all)


if __name__ == "__main__":
    main()
