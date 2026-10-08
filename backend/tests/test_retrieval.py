from app.retrieval.engine import rrf, tokens
from app.retrieval.answer import validate_citations
from app.eval.run import metrics


def test_rrf_rewards_agreement_and_deduplicates() -> None:
    result = rrf([["a", "b", "b"], ["b", "c"]])
    assert result[0][0] == "b"
    assert abs(result[0][1] - (1 / 62 + 1 / 61)) < 1e-10
    assert rrf([]) == []


def test_tokenize_identifiers() -> None:
    assert tokens("getUser_data") == ["get", "user", "data"]


def test_rejects_fabricated_and_out_of_range_citations() -> None:
    answer, citations, warnings = validate_citations(
        "Yes [a.py:4-5]. No [a.py:1-99]. No [fake.py:4-5].", [{"path": "a.py", "start": 3, "end": 8}]
    )
    assert citations == [{"path": "a.py", "start": 4, "end": 5}]
    assert len(warnings) == 2
    assert "[fake.py:" not in answer


def test_eval_requires_symbol_not_only_filename() -> None:
    chunks = [{"path": "a.py", "symbol": "wrong"}, {"path": "a.py", "symbol": "right"}]
    assert metrics(chunks, [{"path": "a.py", "symbol": "right"}]) == (1.0, 0.5)


def test_unicode_citation_dash_is_normalized() -> None:
    answer, citations, warnings = validate_citations(
        "Found [a.py:4\u20115]", [{"path": "a.py", "start": 3, "end": 8}]
    )
    assert answer == "Found [a.py:4-5]"
    assert len(citations) == 1
    assert warnings == []
