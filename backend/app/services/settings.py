from ..core import redis
from ..core.config import settings
from ..crud import get_user_settings, save_user_settings

USER_SETTINGS_CACHE_TTL_SECONDS = settings.user_settings_cache_ttl_seconds


def _settings_cache_key(user_id):
    return f"user:settings:{user_id}"


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
        "api_key": user_settings.api_key,
        "provider": user_settings.provider or "deepseek",
        "embedding_api_key": user_settings.embedding_api_key,
        "embedding_base_url": user_settings.embedding_base_url or settings.rag_embedding_base_url,
        "embedding_model": user_settings.embedding_model or settings.rag_embedding_model,
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}


def save_settings_service(db, user, request):
    api_key = (request.api_key or "").strip()
    provider = (request.provider or "deepseek").strip().lower() or "deepseek"
    embedding_api_key = (request.embedding_api_key or "").strip()
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
        "api_key": user_settings.api_key,
        "provider": user_settings.provider,
        "embedding_api_key": user_settings.embedding_api_key,
        "embedding_base_url": user_settings.embedding_base_url or settings.rag_embedding_base_url,
        "embedding_model": user_settings.embedding_model or settings.rag_embedding_model,
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}
