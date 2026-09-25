import numpy as np
import pytest

from app.embed import EMBED_DIM, cosine_similarity, embed_aligned
from model_guard import requires_models


def test_cosine_identical_vectors():
    v = np.array([3.0, 4.0, 0.0], dtype=np.float32)
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_scale_invariance():
    assert cosine_similarity([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one():
    assert cosine_similarity([1.0, 0.0], [-2.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_defined_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_symmetry():
    a, b = [1.0, 2.0, 3.0], [-1.0, 5.0, 0.5]
    assert cosine_similarity(a, b) == pytest.approx(cosine_similarity(b, a))


def test_cosine_never_exceeds_one_on_random_self_pairs():
    rng = np.random.default_rng(0)
    for _ in range(200):
        v = rng.standard_normal(EMBED_DIM).astype(np.float32)
        s = cosine_similarity(v, v)
        assert s <= 1.0, f"score {s!r} escaped the contract range"
        assert s == pytest.approx(1.0)


def test_cosine_never_below_minus_one_on_random_opposite_pairs():
    rng = np.random.default_rng(1)
    for _ in range(200):
        v = rng.standard_normal(EMBED_DIM).astype(np.float32)
        s = cosine_similarity(v, -v)
        assert s >= -1.0, f"score {s!r} escaped the contract range"
        assert s == pytest.approx(-1.0)


def test_cosine_clamps_a_value_pushed_past_one():
    v = np.array([0.1, 0.2, 0.3] * 100, dtype=np.float32)
    v = v / np.linalg.norm(v)
    assert -1.0 <= cosine_similarity(v, v) <= 1.0


def test_cosine_stays_in_range_for_arbitrary_pairs():
    rng = np.random.default_rng(2)
    for _ in range(200):
        a = rng.standard_normal(EMBED_DIM).astype(np.float32)
        b = rng.standard_normal(EMBED_DIM).astype(np.float32)
        assert -1.0 <= cosine_similarity(a, b) <= 1.0


@requires_models
def test_embed_output_shape_dtype_norm():
    aligned = np.full((112, 112, 3), 128, np.uint8)
    e = embed_aligned(aligned)
    assert e.shape == (EMBED_DIM,)
    assert EMBED_DIM == 512
    assert e.dtype == np.float32
    assert np.linalg.norm(e) == pytest.approx(1.0, abs=1e-5)


@requires_models
def test_embed_is_deterministic():
    aligned = np.full((112, 112, 3), 128, np.uint8)
    assert np.array_equal(embed_aligned(aligned), embed_aligned(aligned))


@requires_models
def test_embed_different_content_differs():
    stripes_h = np.tile(np.array([[0, 255]] * 56, np.uint8), (1, 56))
    stripes_v = np.tile(np.array([[0], [255]] * 56, np.uint8), (1, 112))
    a = embed_aligned(np.repeat(stripes_h[:, :, None], 3, axis=2))
    b = embed_aligned(np.repeat(stripes_v[:, :, None], 3, axis=2))
    assert cosine_similarity(a, b) < 0.99
