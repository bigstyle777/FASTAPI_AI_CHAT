from ...core import redis
from ...core.config import settings
from ..repository import get_user_settings, save_user_settings

USER_SETTINGS_CACHE_TTL_SECONDS = settings.user_settings_cache_ttl_seconds

# 掩码前缀：保存时前端若把掩码原样回传，后端据此识别"用户没改"。
_KEY_MASK_PREFIX = "***"


def _settings_cache_key(user_id):
    return f"user:settings:{user_id}"


def _mask_key(key: str | None) -> str | None:
    """对 Key 做掩码，只留末尾 4 位用于用户辨认。

    回显给前端、缓存进 Redis 的都是掩码版，避免真实 Key 通过
    GET /settings 或缓存层泄露（XSS 拿到 token 后可调 /settings）。
    """
    if not key:
        return None
    if len(key) <= 4:
        return _KEY_MASK_PREFIX + "****"
    return _KEY_MASK_PREFIX + key[-4:]


def _resolve_key_on_save(submitted: str | None, current: str | None) -> str | None:
    """保存时决定最终写入的 Key 值。

    - submitted 等于现有 Key 的掩码：用户没改，保留原值；
    - submitted 为空：清空；
    - 其他：当作新 Key 写入。
    """
    submitted = (submitted or "").strip()
    if submitted == _mask_key(current):
        return current
    return submitted or None


def _cache_settings(user_id, payload):
    redis.redis_set_json(
        _settings_cache_key(user_id),
        payload,
        ttl=USER_SETTINGS_CACHE_TTL_SECONDS,
    )


def _default_settings_payload():
    return {
        "api_key": None,
        "provider": "deepseek",
        "embedding_api_key": None,
        "embedding_base_url": settings.rag_embedding_base_url,
        "embedding_model": settings.rag_embedding_model,
    }


def get_settings_service(db, user):
    cached_settings = redis.redis_get_json(_settings_cache_key(user["user_id"]))
    if cached_settings is not None:
        return {"success": True, **cached_settings}

    user_settings = get_user_settings(db, user["user_id"])
    if not user_settings:
        result = _default_settings_payload()
        _cache_settings(user["user_id"], result)
        return {"success": True, **result}

    result = {
        "api_key": _mask_key(user_settings.api_key),
        "provider": user_settings.provider or "deepseek",
        "embedding_api_key": _mask_key(user_settings.embedding_api_key),
        "embedding_base_url": user_settings.embedding_base_url or settings.rag_embedding_base_url,
        "embedding_model": user_settings.embedding_model or settings.rag_embedding_model,
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}


def save_settings_service(db, user, request):
    existing = get_user_settings(db, user["user_id"])

    api_key = _resolve_key_on_save(request.api_key, existing.api_key if existing else None)
    embedding_api_key = _resolve_key_on_save(
        request.embedding_api_key, existing.embedding_api_key if existing else None
    )

    provider = (request.provider or "deepseek").strip().lower() or "deepseek"
    embedding_base_url = (request.embedding_base_url or "").strip()
    embedding_model = (request.embedding_model or "").strip()

    user_settings = save_user_settings(
        db,
        user["user_id"],
        api_key,
        provider,
        embedding_api_key=embedding_api_key,
        embedding_base_url=embedding_base_url,
        embedding_model=embedding_model,
    )
    result = {
        "api_key": _mask_key(user_settings.api_key),
        "provider": user_settings.provider,
        "embedding_api_key": _mask_key(user_settings.embedding_api_key),
        "embedding_base_url": user_settings.embedding_base_url or settings.rag_embedding_base_url,
        "embedding_model": user_settings.embedding_model or settings.rag_embedding_model,
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}
