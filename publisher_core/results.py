from dataclasses import dataclass, field
from typing import List


@dataclass
class RenderResult:
    success: bool
    logs: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    files: List[str] = field(default_factory=list)
