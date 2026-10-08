import pytest
from app.models.schema import github_url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/a/b",
        "https://evil.com/a/b",
        "https://github.com@evil.com/a/b",
        "https://github.com/../x",
        "file:///etc/passwd",
        "https://github.com/a/b?x=1",
        "https://github.com/a/b/tree/main",
    ],
)
def test_reject_bad_urls(url: str) -> None:
    with pytest.raises(ValueError):
        github_url(url)


def test_normalize_github_url() -> None:
    assert (
        github_url("https://github.com/pallets/itsdangerous.git/")
        == "https://github.com/pallets/itsdangerous"
    )
