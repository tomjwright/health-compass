"""Compare a source's published data model with the snapshot saved in the repo."""

from dataclasses import dataclass
from typing import Any


@dataclass
class SchemaDiff:
    added: list[str]
    removed: list[str]
    changed: list[str]

    @property
    def is_breaking(self) -> bool:
        """New fields are safe to ignore; removed fields or new types can break our models."""
        return bool(self.removed or self.changed)


def flatten(nodes: list[dict[str, Any]], prefix: str = "") -> dict[str, str]:
    """Turn a nested field tree into {"a.b.c": "sourceType:type"}."""
    fields = {}
    for node in nodes:
        path = prefix + node["name"]
        fields[path] = f"{node['sourceType']}:{node['type']}"
        fields.update(flatten(node.get("children", []), path + "."))
    return fields


def compare(snapshot: dict[str, str], current: dict[str, str]) -> SchemaDiff:
    return SchemaDiff(
        added=sorted(current.keys() - snapshot.keys()),
        removed=sorted(snapshot.keys() - current.keys()),
        changed=sorted(
            path for path in snapshot.keys() & current.keys() if snapshot[path] != current[path]
        ),
    )
