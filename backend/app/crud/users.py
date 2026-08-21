from sqlalchemy import select

from ..models import User


def get_user_by_username(db, username):
    stmt = select(User).where(User.username == username)
    return db.execute(stmt).scalar_one_or_none()


def get_user_by_id(db, user_id):
    stmt = select(User).where(User.id == user_id)
    return db.execute(stmt).scalar_one_or_none()


def create_user(db, username, password, role_id):
    user = User(username=username, password=password, role_id=role_id)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def get_users_with_roles(db):
    stmt = select(User).order_by(User.id.asc())
    return db.execute(stmt).scalars().all()