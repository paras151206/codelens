import pytest
from app.ingestion.chunker import chunk_file


def test_python_symbols_and_original_lines() -> None:
    text = 'import os\n\nclass Greeter:\n    def hello(self):\n        return "hi"\n'
    chunks = chunk_file("greet.py", text)
    method = next(c for c in chunks if c.symbol == "hello")
    assert (method.start, method.end, method.parent) == (4, 5, "Greeter")
    assert method.text == "\n".join(text.splitlines()[3:5])
    assert method.imports == ["import os"]
    assert any(c.symbol == "module" and c.start == 1 for c in chunks)


@pytest.mark.parametrize(
    "path,text,symbol",
    [
        ("x.ts", "export function sum(a: number) { return a + 1; }", "sum"),
        ("x.js", "function sum(a) { return a + 1; }", "sum"),
        ("x.java", "class X { public int sum(int a) { return a + 1; } }", "sum"),
        ("x.c", "int sum(int a) { return a + 1; }", "sum"),
        ("x.cpp", "int sum(int a) { return a + 1; }", "sum"),
        ("x.go", "package x\nfunc sum(a int) int { return a + 1 }", "sum"),
        ("x.rs", "fn sum(a: i32) -> i32 { a + 1 }", "sum"),
    ],
)
def test_supported_languages(path: str, text: str, symbol: str) -> None:
    assert any(c.symbol == symbol for c in chunk_file(path, text))


def test_fallback_covers_every_line_and_is_bounded() -> None:
    chunks = chunk_file("notes.md", "\n".join(f"line {i}" for i in range(200)))
    covered = {i for c in chunks for i in range(c.start, c.end + 1)}
    assert covered == set(range(1, 201))
    assert all(c.end - c.start < 65 for c in chunks)
    assert len({c.id for c in chunks}) == len(chunks)
