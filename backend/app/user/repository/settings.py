from datetime import datetime

from sqlalchemy import select

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
    settings = get_user_settings(db, user_id)
    if settings:
        settings.api_key = api_key
        settings.provider = provider
        # embedding_api_key=None 表示不更新该字段；空串/非空串均会写入（空串视作清空）
        settings.embedding_api_key = embedding_api_key or None
        settings.embedding_base_url = embedding_base_url or None
        settings.embedding_model = embedding_model or None
        settings.updated_at = datetime.now()
    else:
        settings = UserSetting(
            user_id=user_id,
            api_key=api_key,
            provider=provider,
            embedding_api_key=embedding_api_key or None,
            embedding_base_url=embedding_base_url or None,
            embedding_model=embedding_model or None,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
        db.add(settings)

    db.commit()
    db.refresh(settings)
    return settings