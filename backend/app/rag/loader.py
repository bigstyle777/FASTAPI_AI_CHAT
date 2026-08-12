from pathlib import Path

from .parsers import get_parser


def load_text_from_file(path: str | Path, filename: str) -> str:
    parser = get_parser(filename)
    return parser.parse(path)
