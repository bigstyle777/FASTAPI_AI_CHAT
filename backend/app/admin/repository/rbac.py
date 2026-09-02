from sqlalchemy import select

from ..models import Permission, Role, RolePermission


def get_role_by_name(db, role_name):
    stmt = select(Role).where(Role.name == role_name)
    return db.execute(stmt).scalar_one_or_none()


def get_role_by_id(db, role_id):
    stmt = select(Role).where(Role.id == role_id)
    return db.execute(stmt).scalar_one_or_none()


def get_roles(db):
    stmt = select(Role).order_by(Role.id.asc())
    return db.execute(stmt).scalars().all()


def create_role(db, name, description=None, is_system=False):
    role = Role(name=name, description=description, is_system=is_system)
    db.add(role)
    db.commit()
    db.refresh(role)
    return role


def get_permission_by_code(db, code):
    stmt = select(Permission).where(Permission.code == code)
    return db.execute(stmt).scalar_one_or_none()


def get_permission_by_id(db, permission_id):
    stmt = select(Permission).where(Permission.id == permission_id)
    return db.execute(stmt).scalar_one_or_none()


def get_permissions(db):
    stmt = select(Permission).order_by(Permission.id.asc())
    return db.execute(stmt).scalars().all()


def create_permission(db, code, name, description=None):
    permission = Permission(code=code, name=name, description=description)
    db.add(permission)
    db.commit()
    db.refresh(permission)
    return permission


def add_permission_to_role(db, role_id, permission_id):
    stmt = select(RolePermission).where(
        RolePermission.role_id == role_id,
        RolePermission.permission_id == permission_id,
    )
    existing = db.execute(stmt).scalar_one_or_none()
    if existing:
        return existing

    link = RolePermission(role_id=role_id, permission_id=permission_id)
    db.add(link)
    db.commit()
    return link


def replace_role_permissions(db, role_id, permission_ids):
    db.execute(
        RolePermission.__table__.delete().where(RolePermission.role_id == role_id)
    )
    for permission_id in permission_ids:
        db.add(RolePermission(role_id=role_id, permission_id=permission_id))
    db.commit()