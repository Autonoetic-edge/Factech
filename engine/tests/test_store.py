import uuid

import numpy as np
import pytest

from app import store


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


def test_enroll_returns_unique_uuid4():
    emb = np.ones(512, dtype=np.float32)
    tid1 = store.enroll("alice", emb)
    tid2 = store.enroll("alice", emb)
    uuid.UUID(tid1)
    uuid.UUID(tid2)
    assert tid1 != tid2


def test_enroll_preserves_embedding():
    emb = np.linspace(-1.0, 1.0, 512).astype(np.float32)
    tid = store.enroll("alice", emb)
    templates = store.get_templates("alice")
    assert [t["template_id"] for t in templates] == [tid]
    assert np.array_equal(templates[0]["embedding"], emb)


def test_has_user_and_get_templates():
    assert not store.has_user("alice")
    assert store.get_templates("alice") == []
    store.enroll("alice", np.zeros(512, np.float32))
    assert store.has_user("alice")
    assert len(store.get_templates("alice")) == 1


def test_multiple_templates_accumulate():
    store.enroll("alice", np.zeros(512, np.float32))
    store.enroll("alice", np.ones(512, np.float32))
    templates = store.get_templates("alice")
    assert len(templates) == 2


def test_users_are_isolated():
    store.enroll("alice", np.zeros(512, np.float32))
    store.enroll("bob", np.ones(512, np.float32))
    assert len(store.get_templates("alice")) == 1
    assert len(store.get_templates("bob")) == 1
    assert not store.has_user("carol")


def test_reset_wipes_everything():
    store.enroll("alice", np.zeros(512, np.float32))
    store.reset()
    assert not store.has_user("alice")
    assert store.get_templates("alice") == []
