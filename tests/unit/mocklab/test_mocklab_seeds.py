import pytest

from rastro.mocklab import DeterministicStream


def test_same_seed_and_namespace_reproduce_and_others_differ():
    def draw(seed, namespace):
        stream = DeterministicStream(seed, namespace)
        return stream.letters(100), stream.integer(0, 10**9)

    assert draw(1, "a") == draw(1, "a")
    assert draw(1, "a") != draw(2, "a")
    assert draw(1, "a") != draw(1, "b")


def test_placeholder_preserves_length_and_whitespace_only():
    original = "Secret  text\n\twith tabs"
    replaced = DeterministicStream(0, "t").placeholder(original)
    assert len(replaced) == len(original)
    assert [c.isspace() for c in replaced] == [c.isspace() for c in original]
    assert replaced.replace(" ", "").replace("\n", "").replace("\t", "").isalpha()
    assert "Secret" not in replaced


def test_integer_and_choice_bounds():
    stream = DeterministicStream(3, "n")
    values = {stream.integer(5, 7) for _ in range(200)}
    assert values == {5, 6, 7}
    assert stream.choice(["only"]) == "only"
    with pytest.raises(ValueError):
        stream.integer(2, 1)
    with pytest.raises(ValueError):
        stream.choice([])
