import logging


logger = logging.getLogger(__name__)


def enqueue_session_title_generation(
    session_id: int, message: str, user_id: int
) -> bool:
    try:
        from .title_tasks import generate_session_title_task

        generate_session_title_task.apply_async(
            args=(session_id, message, user_id),
            retry=False,
        )
        return True
    except Exception:
        logger.exception("Failed to enqueue session title generation")
        return False
