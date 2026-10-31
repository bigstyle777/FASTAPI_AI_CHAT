"""敏感字段对称加密：API Key 等存进 DB 前加密，读出来再解密。

为什么不用 JWT_SECRET_KEY 当密钥
--------------------------------
dev 环境下 _validate_jwt_secret 会在启动时把默认密钥随机替换，导致
每次启动派生出的 Fernet 密钥不同，上次加密的值这次解不开。所以
加密密钥独立成 DATA_ENCRYPTION_KEY，由部署方显式提供。

向后兼容
--------
加密值带 ``enc::`` 前缀，旧明文没有前缀。decrypt 遇到无前缀的值
原样返回（下次保存时会被加密），平滑迁移无需停机。

未配置 DATA_ENCRYPTION_KEY 时降级为明文存储（行为同过去），并打一次
警告，提醒生产环境应配置。"""
from __future__ import annotations

import base64
import hashlib
import logging

from cryptography.fernet import Fernet, InvalidToken

from .config import settings

logger = logging.getLogger(__name__)

_PREFIX = "enc::"
_warned_no_key = False


def _fernet() -> Fernet | None:
    key = settings.data_encryption_key
    if not key:
        global _warned_no_key
        if not _warned_no_key:
            logger.warning(
                "DATA_ENCRYPTION_KEY 未配置，用户 API Key 将以明文存储。"
                "生产环境请在 .env 中设置一个强随机密钥。"
            )
            _warned_no_key = True
        return None
    # 从任意长度的密钥派生 32 字节（Fernet 要求 urlsafe base64 的 32 字节）
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str | None) -> str | None:
    """加密明文，返回带 enc:: 前缀的密文；None 原样返回。"""
    if not plaintext:
        return None
    f = _fernet()
    if f is None:
        return plaintext  # 未配置密钥，明文存储
    token = f.encrypt(plaintext.encode("utf-8")).decode("ascii")
    return _PREFIX + token


def decrypt(stored: str | None) -> str | None:
    """解密带前缀的密文；无前缀的旧明文原样返回（兼容）。"""
    if not stored:
        return None
    if not stored.startswith(_PREFIX):
        return stored  # 旧明文，原样返回
    f = _fernet()
    if f is None:
        # 密钥被撤掉后读不到旧密文，当作未配置
        return None
    try:
        return f.decrypt(stored[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken:
        logger.warning("用户密钥解密失败（密钥已变更或数据损坏），当作未配置")
        return None
