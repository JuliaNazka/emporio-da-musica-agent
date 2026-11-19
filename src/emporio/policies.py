"""Search the store policy manual: the PDF is split by section and ranked with BM25."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from rank_bm25 import BM25Okapi

from emporio.data import DEFAULT_DATA_DIR, normalize_text

POLICY_PDF = DEFAULT_DATA_DIR / "políticas_da_loja.pdf"
HEADING = re.compile(r"^(\d{1,2}(?:\.\d{1,2})?)\.?\s+([A-ZÀ-Ý].*)$")
PAGE_NOISE = re.compile(r"^(Empório da Música Manual de Políticas e Procedimentos|Página \d+)$")
TOP_K = 3
STEM_LENGTH = 5
STOPWORDS = frozenset(
    "a o as os e de da do das dos em no na nos nas um uma uns umas para pra por com sem que se "
    "ao aos eu voce voces posso pode qual quais como meu minha meus minhas sua seu tem ter "
    "esta este isso essa esse vou quero".split()
)


@dataclass(frozen=True)
class PolicySection:
    section: str
    title: str
    topic: str
    text: str


def read_manual(path: Path = POLICY_PDF) -> str:
    return "\n".join(page.extract_text(extraction_mode="layout") for page in PdfReader(path).pages)


def split_sections(manual: str) -> list[PolicySection]:
    """Headings start at column 0 ("4. Title" or "4.2 Title"); indented numbered lists are body text."""
    parsed: list[tuple[str, str, list[str]]] = []
    for raw_line in manual.splitlines():
        line = " ".join(raw_line.split())
        if not line or PAGE_NOISE.match(line):
            continue
        heading = HEADING.match(raw_line.rstrip())
        if heading:
            parsed.append((heading.group(1), heading.group(2).strip(), []))
        elif parsed:
            parsed[-1][2].append(line)

    topics = {number: title for number, title, _ in parsed if "." not in number}
    return [
        PolicySection(number, title, topics.get(number.split(".")[0], title), " ".join(body))
        for number, title, body in parsed
        if body
    ]


def _stem(word: str) -> str:
    """Prefix stemming, so "devolver", "devolução" and "devoluções" share a token."""
    return word[:STEM_LENGTH]


def _search_tokens(text: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", normalize_text(text))
    return [_stem(word) for word in words if word not in STOPWORDS]


class PolicyIndex:
    def __init__(self, sections: list[PolicySection]):
        self.sections = sections
        corpus = [_search_tokens(f"{s.topic} {s.title} {s.text}") for s in sections]
        self._bm25 = BM25Okapi(corpus)

    @classmethod
    def from_pdf(cls, path: Path = POLICY_PDF) -> PolicyIndex:
        return cls(split_sections(read_manual(path)))

    def search(self, query: str, top_k: int = TOP_K) -> list[dict]:
        scores = self._bm25.get_scores(_search_tokens(query))
        ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            {
                "section": self.sections[i].section,
                "title": self.sections[i].title,
                "text": self.sections[i].text,
                "score": round(float(scores[i]), 2),
            }
            for i in ranked[:top_k]
            if scores[i] > 0
        ]


def search_policies(index: PolicyIndex, query: str) -> dict:
    results = index.search(query)
    if not results:
        return {"found": False, "message": "Nenhuma seção do manual trata desse assunto."}
    return {"found": True, "results": results}
