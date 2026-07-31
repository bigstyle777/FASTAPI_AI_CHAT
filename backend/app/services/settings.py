from ..core.config import settings
from ..core.redis import redis_get_json, redis_set_json
from ..crud import get_user_settings, save_user_settings

USER_SETTINGS_CACHE_TTL_SECONDS = settings.user_settings_cache_ttl_seconds


def _settings_cache_key(user_id):
    return f"user:settings:{user_id}"


def _cache_settings(user_id, settings):
    redis_set_json(
        _settings_cache_key(user_id),
        settings,
        ttl=USER_SETTINGS_CACHE_TTL_SECONDS,
    )


def get_settings_service(db, user):
    cached_settings = redis_get_json(_settings_cache_key(user["user_id"]))
    if cached_settings is not None:
        return {"success": True, **cached_settings}

    settings = get_user_settings(db, user["user_id"])
    if not settings:
        result = {"api_key": None, "provider": "deepseek"}
        _cache_settings(user["user_id"], result)
        return {"success": True, **result}

    result = {
        "api_key": settings.api_key,
        "provider": settings.provider or "deepseek",
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}


def save_settings_service(db, user, request):
    api_key = (request.api_key or "").strip()
    provider = (request.provider or "deepseek").strip().lower() or "deepseek"
    settings = save_user_settings(db, user["user_id"], api_key, provider)
    result = {
        "api_key": settings.api_key,
        "provider": settings.provider,
    }
    _cache_settings(user["user_id"], result)
    return {"success": True, **result}
