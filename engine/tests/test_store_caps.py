import numpy as np
import pytest

from app import store


@pytest.fixture(autouse=True)
def _clean_store():
    store.reset()
    yield
    store.reset()


@pytest.fixture
def small_caps(monkeypatch):
    monkeypatch.setattr(store, "MAX_TEMPLATES_PER_USER", 3)
    monkeypatch.setattr(store, "MAX_USERS", 4)


def _emb(n: int):
    return np.full(512, float(n), dtype=np.float32)


def _tags(user_id: str) -> list[float]:
    return [float(t["embedding"][0]) for t in store.get_templates(user_id)]


def test_production_caps_are_what_the_contract_says():
    assert store.MAX_TEMPLATES_PER_USER == 5
    assert store.MAX_USERS == 1000


def test_per_user_cap_holds_at_the_real_value():
    for i in range(store.MAX_TEMPLATES_PER_USER + 1):
        store.enroll("alice", _emb(i))
    assert len(store.get_templates("alice")) == store.MAX_TEMPLATES_PER_USER


def test_templates_accumulate_up_to_the_cap(small_caps):
    for i in range(3):
        store.enroll("alice", _emb(i))
    assert _tags("alice") == [0, 1, 2]


def test_oldest_template_is_evicted_first(small_caps):
    for i in range(5):
        store.enroll("alice", _emb(i))
    assert _tags("alice") == [2, 3, 4], "expected the two oldest to be evicted"


def test_enroll_past_the_cap_still_succeeds(small_caps):
    ids = [store.enroll("alice", _emb(i)) for i in range(5)]
    assert len(set(ids)) == 5
    kept = {t["template_id"] for t in store.get_templates("alice")}
    assert kept == set(ids[2:]), "the returned ids that survive are the newest"


def test_cap_is_per_user_not_global(small_caps):
    for i in range(4):
        store.enroll("alice", _emb(i))
        store.enroll("bob", _emb(i))
    assert len(store.get_templates("alice")) == 3
    assert len(store.get_templates("bob")) == 3


def test_lowering_the_cap_converges_on_the_next_enroll(monkeypatch):
    monkeypatch.setattr(store, "MAX_TEMPLATES_PER_USER", 5)
    for i in range(5):
        store.enroll("alice", _emb(i))
    monkeypatch.setattr(store, "MAX_TEMPLATES_PER_USER", 2)
    store.enroll("alice", _emb(99))
    assert _tags("alice") == [4, 99]


def test_users_accumulate_up_to_the_cap(small_caps):
    for i in range(4):
        store.enroll(f"u{i}", _emb(i))
    assert store.user_count() == 4
    assert all(store.has_user(f"u{i}") for i in range(4))


def test_oldest_user_is_evicted_whole(small_caps):
    for i in range(4):
        store.enroll(f"u{i}", _emb(i))
        store.enroll(f"u{i}", _emb(i))
    store.enroll("newcomer", _emb(9))

    assert store.user_count() == 4
    assert not store.has_user("u0"), "oldest user should have been evicted"
    assert store.get_templates("u0") == []
    assert store.has_user("newcomer")
    assert len(store.get_templates("u1")) == 2, "survivors keep all their templates"


def test_evicted_user_is_simply_unknown_again(small_caps):
    store.enroll("early", _emb(0))
    for i in range(4):
        store.enroll(f"later{i}", _emb(i))
    assert not store.has_user("early")


def test_re_enrolling_an_existing_user_evicts_nobody(small_caps):
    for i in range(4):
        store.enroll(f"u{i}", _emb(i))
    for _ in range(10):
        store.enroll("u0", _emb(7))
    assert store.user_count() == 4
    assert all(store.has_user(f"u{i}") for i in range(4))


def test_user_cap_holds_under_a_flood(small_caps):
    for i in range(200):
        store.enroll(f"flood{i}", _emb(i))
    assert store.user_count() == 4
    assert store.has_user("flood199")


def test_lowering_the_user_cap_converges(monkeypatch):
    monkeypatch.setattr(store, "MAX_USERS", 10)
    for i in range(10):
        store.enroll(f"u{i}", _emb(i))
    monkeypatch.setattr(store, "MAX_USERS", 3)
    store.enroll("newcomer", _emb(99))
    assert store.user_count() == 3
    assert store.has_user("newcomer")


def test_delete_user_returns_how_many_it_removed():
    store.enroll("alice", _emb(0))
    store.enroll("alice", _emb(1))
    assert store.delete_user("alice") == 2
    assert not store.has_user("alice")


def test_delete_user_on_an_unknown_user_is_zero_not_an_error():
    assert store.delete_user("ghost") == 0
    assert store.delete_user("ghost") == 0


def test_delete_user_leaves_other_users_alone():
    store.enroll("alice", _emb(0))
    store.enroll("bob", _emb(1))
    store.delete_user("alice")
    assert store.user_count() == 1
    assert _tags("bob") == [1]


def test_delete_user_frees_a_slot_under_the_user_cap(small_caps):
    for i in range(4):
        store.enroll(f"u{i}", _emb(i))
    store.delete_user("u0")
    store.enroll("newcomer", _emb(9))
    assert store.user_count() == 4
    assert store.has_user("u1"), "no eviction was needed — a slot was free"
    assert store.has_user("newcomer")


def test_deleted_user_can_enroll_again():
    store.enroll("alice", _emb(0))
    store.delete_user("alice")
    store.enroll("alice", _emb(1))
    assert _tags("alice") == [1], "re-enrollment starts clean, not appended"
