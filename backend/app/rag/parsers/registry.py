from pathlib import Path

from ...exceptions import BusinessError
from .base import DocumentParser
from .text import TextDocumentParser


PARSERS: tuple[DocumentParser, ...] = (
    TextDocumentParser(),
)


def get_parser(filename: str) -> DocumentParser:
    suffix = Path(filename).suffix.lower()
    for parser in PARSERS:
        if suffix in parser.suffixes:
            return parser

    supported = ", ".join(sorted(supported_suffixes()))
    raise BusinessError(f"RAG only supports these file types: {supported}")


def supported_suffixes() -> set[str]:
    suffixes: set[str] = set()
    for parser in PARSERS:
        suffixes.update(parser.suffixes)
    return suffixes
