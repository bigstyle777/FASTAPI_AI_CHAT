from pathlib import Path

from ...exceptions import BusinessError
from .base import DocumentParser


class TextDocumentParser(DocumentParser):
    suffixes = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}

    def parse(self, path: str | Path) -> str:
        raw = Path(path).read_bytes()
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise BusinessError("Cannot detect file encoding. Please upload a UTF-8 text file.")

        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            raise BusinessError("Document content is empty")
        return text
