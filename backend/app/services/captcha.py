import base64
import random
import uuid

from ..core import redis
from ..core.config import settings

CAPTCHA_TTL_SECONDS = settings.captcha_ttl_seconds
CAPTCHA_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_random = random.SystemRandom()


def _captcha_key(captcha_id):
    return f"auth:captcha:{captcha_id}"


def _create_captcha_code(length=5):
    return "".join(_random.choice(CAPTCHA_ALPHABET) for _ in range(length))


def _create_captcha_image(code):
    text_items = []
    for index, char in enumerate(code):
        x = 18 + index * 23
        y = 34 + _random.randint(-3, 4)
        rotate = _random.randint(-14, 14)
        color = _random.choice(["#111827", "#0f766e", "#1d4ed8", "#7c2d12"])
        text_items.append(
            f'<text x="{x}" y="{y}" transform="rotate({rotate} {x} {y})" fill="{color}">{char}</text>'
        )

    line_items = []
    for _ in range(4):
        x1 = _random.randint(4, 120)
        y1 = _random.randint(8, 42)
        x2 = _random.randint(4, 120)
        y2 = _random.randint(8, 42)
        color = _random.choice(["#94a3b8", "#99f6e4", "#bfdbfe"])
        line_items.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="1" opacity="0.7"/>'
        )

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="136" height="48" viewBox="0 0 136 48">'
        '<rect width="136" height="48" rx="8" fill="#f8fafc"/>'
        f'{"".join(line_items)}'
        '<g font-family="Arial, sans-serif" font-size="24" font-weight="700" letter-spacing="2">'
        f'{"".join(text_items)}'
        "</g>"
        "</svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


def create_captcha_service():
    captcha_id = str(uuid.uuid4())
    code = _create_captcha_code()
    redis.redis_set(_captcha_key(captcha_id), code.lower(), ttl=CAPTCHA_TTL_SECONDS)
    return {
        "success": True,
        "captcha_id": captcha_id,
        "image": _create_captcha_image(code),
        "expires_in": CAPTCHA_TTL_SECONDS,
    }


def _verify_captcha(captcha_id, captcha_code):
    if not captcha_id or not captcha_code:
        return False

    key = _captcha_key(captcha_id)
    stored_code = redis.redis_get(key)
    if stored_code is None:
        return False

    if stored_code != captcha_code.strip().lower():
        return False

    redis.redis_delete(key)
    return True
