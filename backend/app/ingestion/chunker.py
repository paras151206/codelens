import hashlib
import re
from pathlib import Path
from app.models.schema import Chunk

LANGUAGES = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".go": "go",
    ".rs": "rust",
}
SYMBOLS = {
    "function_definition",
    "class_definition",
    "function_declaration",
    "method_definition",
    "class_declaration",
    "method_declaration",
    "function_item",
    "impl_item",
    "struct_item",
    "interface_declaration",
    "lexical_declaration",
}


def chunk_file(path: str, text: str, window: int = 65) -> list[Chunk]:
    language = LANGUAGES.get(Path(path).suffix, "text")
    lines = text.splitlines()
    imports = [
        line.strip()
        for line in lines
        if re.match(r"\s*(import |from |use |#include|const .*require\()", line)
    ][:40]
    spans: list[tuple[int, int, str, str]] = []
    if language != "text":
        from tree_sitter_language_pack import get_parser

        tree = get_parser(language).parse(text.encode())

        def walk(node, parent: str = "") -> None:
            current = parent
            if node.type in SYMBOLS:
                name = node.child_by_field_name("name")
                if name is None:
                    declarator = node.child_by_field_name("declarator")
                    if declarator is None and node.type == "lexical_declaration":
                        declarator = next((c for c in node.children if c.type == "variable_declarator"), None)
                    while declarator is not None:
                        name = declarator.child_by_field_name("name")
                        if name is not None:
                            break
                        if declarator.type in {"identifier", "field_identifier"}:
                            name = declarator
                            break
                        declarator = declarator.child_by_field_name("declarator")
                symbol = name.text.decode(errors="replace") if name else node.type
                end = node.end_point.row + (1 if node.end_point.column else 0)
                spans.append((node.start_point.row + 1, max(node.start_point.row + 1, end), symbol, parent))
                if "class" in node.type or node.type in {"impl_item", "struct_item"}:
                    current = symbol
            for child in node.children:
                walk(child, current)

        walk(tree.root_node)
    covered: set[int] = set()
    result: list[Chunk] = []

    def add(start: int, end: int, symbol: str, parent: str) -> None:
        for begin in range(start, end + 1, window - 12):
            finish = min(end, begin + window - 1)
            body = "\n".join(lines[begin - 1 : finish])
            if body.strip():
                key = hashlib.sha256(f"{path}:{begin}:{finish}:{symbol}".encode()).hexdigest()[:32]
                result.append(Chunk(key, path, language, symbol, begin, finish, body, parent, imports))
            if finish == end:
                break

    for start, end, symbol, parent in spans:
        add(start, end, symbol, parent)
        covered.update(range(start, end + 1))
    start = 1
    while start <= len(lines):
        if start in covered:
            start += 1
            continue
        end = start
        while end < len(lines) and end + 1 not in covered:
            end += 1
        add(start, end, "module", "")
        start = end + 1
    return result
