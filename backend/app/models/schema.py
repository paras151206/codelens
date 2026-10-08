from dataclasses import dataclass, field, asdict
from pydantic import BaseModel, Field, field_validator
import re


def github_url(value: str) -> str:
    match = re.fullmatch(
        r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", value.strip()
    )
    if not match or any(x in {".", ".."} for x in match.groups()):
        raise ValueError("Use a public https://github.com/owner/repository URL")
    return f"https://github.com/{match[1]}/{match[2]}"


class IngestRequest(BaseModel):
    url: str
    _url = field_validator("url")(github_url)


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=3000)
    conversation: str = Field(default="", max_length=64, pattern=r"^[a-zA-Z0-9-]*$")


@dataclass
class Chunk:
    id: str
    path: str
    language: str
    symbol: str
    start: int
    end: int
    text: str
    parent: str = ""
    imports: list[str] = field(default_factory=list)

    @property
    def contextual(self) -> str:
        return f"File: {self.path}\nSymbol: {self.parent}.{self.symbol}\n{self.text}"

    def dict(self) -> dict:
        return asdict(self)
