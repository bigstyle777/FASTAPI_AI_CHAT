from pathlib import Path

from ..exceptions import BusinessError


SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".json", ".log"}


def load_text_from_file(path: str | Path, filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise BusinessError("RAG 暂只支持 txt、md、csv、json、log 文本文件")

    raw = Path(path).read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise BusinessError("无法识别文件编码，请上传 UTF-8 文本文件")

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        raise BusinessError("文档内容为空")
    return text
