"""Readable notebook output: answers as formatted text, comparisons as tables."""

from __future__ import annotations

from typing import Any, Iterable


def _ipython():
    from IPython.display import HTML, Markdown, display

    return display, Markdown, HTML


def show(title: str, text: str, caption: str | None = None) -> None:
    """Render an agent answer as formatted Markdown under a heading."""
    display, Markdown, _ = _ipython()
    body = f"### {title}\n\n{text.strip() if text else '_(no answer)_'}"
    if caption:
        body += f"\n\n<sub>{caption}</sub>"
    display(Markdown(body))


def table(rows: Iterable[dict[str, Any]], columns: list[str] | None = None) -> None:
    """Render a list of dictionaries as a compact table."""
    import pandas as pd

    display, _, _ = _ipython()
    frame = pd.DataFrame(list(rows))
    if columns:
        frame = frame[[c for c in columns if c in frame.columns]]
    display(frame.style.hide(axis="index"))


def checklist(results: list[dict[str, Any]]) -> None:
    table(results, ["check", "status", "detail"])
