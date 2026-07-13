from datetime import datetime


def get_user_by_username(db, username):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT *
        FROM users
        WHERE username = ?
        """,
        (username,),
    )
    return cursor.fetchone()


def create_user(db, username, password):
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO users(username, password)
        VALUES(?, ?)
        """,
        (username, password),
    )
    db.commit()


def create_session(db, user_id, title):
    cursor = db.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cursor.execute(
        """
        INSERT INTO chat_sessions(user_id, title, created_at, updated_at)
        VALUES(?, ?, ?, ?)
        """,
        (user_id, title, now, now),
    )
    db.commit()
    return cursor.lastrowid


def create_message(db, session_id, role, content):
    cursor = db.cursor()
    now = datetime.now().isoformat(timespec="seconds")
    cursor.execute(
        """
        INSERT INTO messages(session_id, role, content, created_at)
        VALUES(?, ?, ?, ?)
        """,
        (session_id, role, content, now),
    )
    db.commit()
    return cursor.lastrowid


def update_session(db, session_id, last_message):
    cursor = db.cursor()
    cursor.execute(
        """
        UPDATE chat_sessions
        SET last_message = ?, updated_at = ?
        WHERE id = ?
        """,
        (last_message, datetime.now().isoformat(timespec="seconds"), session_id),
    )
    db.commit()


def get_sessions_by_user(db, user_id):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT id, title, last_message, created_at, updated_at
        FROM chat_sessions
        WHERE user_id = ?
        AND is_deleted = 0
        ORDER BY updated_at DESC, id DESC
        """,
        (user_id,),
    )
    return cursor.fetchall()


def get_session_by_user(db, session_id, user_id):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT *
        FROM chat_sessions
        WHERE id = ?
        AND user_id = ?
        AND is_deleted = 0
        """,
        (session_id, user_id),
    )
    return cursor.fetchone()


def get_messages_by_session(db, session_id):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT role, content
        FROM messages
        WHERE session_id = ?
        ORDER BY id ASC
        """,
        (session_id,),
    )
    return cursor.fetchall()


def get_user_settings(db, user_id):
    cursor = db.cursor()
    cursor.execute(
        """
        SELECT api_key, provider
        FROM user_settings
        WHERE user_id = ?
        """,
        (user_id,),
    )
    return cursor.fetchone()


def save_user_settings(db, user_id, api_key, provider):
    cursor = db.cursor()
    cursor.execute(
        """
        INSERT INTO user_settings(user_id, api_key, provider, updated_at)
        VALUES(?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id) DO UPDATE SET
            api_key = excluded.api_key,
            provider = excluded.provider,
            updated_at = CURRENT_TIMESTAMP
        """,
        (user_id, api_key, provider),
    )
    db.commit()
    return True
