import threading
import uuid

MAX_TEMPLATES_PER_USER = 5

MAX_USERS = 1000

_lock = threading.Lock()

_templates: dict[str, list[dict]] = {}


def enroll(user_id: str, embedding) -> str:
    template_id = str(uuid.uuid4())
    with _lock:
        if user_id not in _templates:
            while len(_templates) >= MAX_USERS:
                _templates.pop(next(iter(_templates)))
        user_templates = _templates.setdefault(user_id, [])
        user_templates.append({"template_id": template_id, "embedding": embedding})

        while len(user_templates) > MAX_TEMPLATES_PER_USER:
            user_templates.pop(0)
    return template_id


def has_user(user_id: str) -> bool:
    with _lock:
        templates = _templates.get(user_id)
        return bool(templates)


def get_templates(user_id: str) -> list[dict]:
    with _lock:
        return list(_templates.get(user_id, []))


def delete_user(user_id: str) -> int:
    with _lock:
        removed = _templates.pop(user_id, [])
    return len(removed)


def user_count() -> int:
    with _lock:
        return len(_templates)


def reset() -> None:
    with _lock:
        _templates.clear()
