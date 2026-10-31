from datetime import datetime

from sqlalchemy import select

from ...core.encryption import encrypt
from ..models import UserSetting


def get_user_settings(db, user_id):
    stmt = select(UserSetting).where(UserSetting.user_id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def save_user_settings(
    db,
    user_id,
    api_key,
    provider,
    embedding_api_key=None,
    embedding_base_url=None,
    embedding_model=None,
):
    # 敏感字段落库前加密（未配置 DATA_ENCRYPTION_KEY 时 encrypt 返回明文，兼容旧行为）
    enc_api_key = encrypt(api_key)
    enc_embedding_key = encrypt(embedding_api_key or None) if embedding_api_key is not None else None

    settings = get_user_settings(db, user_id)
    if settings:
        settings.api_key = enc_api_key
        settings.provider = provider
        # embedding_api_key=None 表示不更新该字段；空串/非空串均会写入（空串视作清空）
        if enc_embedding_key is not None or embedding_api_key is not None:
            settings.embedding_api_key = enc_embedding_key
        settings.embedding_base_url = embedding_base_url or None
        settings.embedding_model = embedding_model or None
        settings.updated_at = datetime.now()
    else:
        settings = UserSetting(
            user_id=user_id,
            api_key=enc_api_key,
            provider=provider,
            embedding_api_key=enc_embedding_key if embedding_api_key is not None else None,
            embedding_base_url=embedding_base_url or None,
            embedding_model=embedding_model or None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(settings)

    db.commit()
    db.refresh(settings)
    return settings