from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Mapping, Optional, Set


@dataclass(frozen=True)
class Formula:
    """A deliberately small proposition AST used by the scaffold domain."""

    kind: str
    name: str = ""
    left: "Optional[Formula]" = None
    right: "Optional[Formula]" = None

    @classmethod
    def atom(cls, name: str) -> "Formula":
        if not name or not name[0].isalpha() or not name.replace("_", "").isalnum():
            raise ValueError(f"invalid atom name: {name!r}")
        return cls(kind="atom", name=name)

    @classmethod
    def implication(cls, left: "Formula", right: "Formula") -> "Formula":
        return cls(kind="imp", left=left, right=right)

    @classmethod
    def conjunction(cls, left: "Formula", right: "Formula") -> "Formula":
        return cls(kind="and", left=left, right=right)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Formula":
        kind = value.get("kind")
        if kind == "atom":
            return cls.atom(str(value["name"]))
        if kind == "imp":
            return cls.implication(
                cls.from_dict(value["left"]), cls.from_dict(value["right"])
            )
        if kind == "and":
            return cls.conjunction(
                cls.from_dict(value["left"]), cls.from_dict(value["right"])
            )
        raise ValueError(f"unsupported formula kind: {kind!r}")

    def to_dict(self) -> Dict[str, Any]:
        if self.kind == "atom":
            return {"kind": self.kind, "name": self.name}
        assert self.left is not None and self.right is not None
        return {
            "kind": self.kind,
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
        }

    def atoms(self) -> Set[str]:
        if self.kind == "atom":
            return {self.name}
        assert self.left is not None and self.right is not None
        return self.left.atoms() | self.right.atoms()

    def complexity(self) -> int:
        if self.kind == "atom":
            return 1
        assert self.left is not None and self.right is not None
        return 1 + self.left.complexity() + self.right.complexity()

    def to_lean(self, parent_precedence: int = 0) -> str:
        if self.kind == "atom":
            return self.name

        assert self.left is not None and self.right is not None
        if self.kind == "and":
            precedence = 35
            rendered = (
                f"{self.left.to_lean(precedence)} ∧ "
                f"{self.right.to_lean(precedence + 1)}"
            )
        elif self.kind == "imp":
            precedence = 20
            rendered = (
                f"{self.left.to_lean(precedence + 1)} → "
                f"{self.right.to_lean(precedence)}"
            )
        else:
            raise ValueError(f"unsupported formula kind: {self.kind!r}")

        if precedence < parent_precedence:
            return f"({rendered})"
        return rendered


def sorted_atoms(formula: Formula) -> Iterable[str]:
    return sorted(formula.atoms())
