"""BrickAgent: Agentic LEGO Design."""

from .catalog import color, name, resolve
from .export import ldraw, write
from .joints import Connector, NoMate, compatible, placement
from .model import Assembly, Occurrence, Placed, attachments, connectors, measure, parts
from .pose import BRICK, ID, PLATE, STUD, UP, Pose, at, move, spin
from .search import describe, find
from .check import check, clashes, connections
from .view import view

__version__ = "0.1.0"

__all__ = [
    "Assembly",
    "BRICK",
    "Connector",
    "ID",
    "NoMate",
    "Occurrence",
    "PLATE",
    "Placed",
    "Pose",
    "STUD",
    "UP",
    "at",
    "attachments",
    "check",
    "clashes",
    "color",
    "compatible",
    "connections",
    "connectors",
    "describe",
    "find",
    "ldraw",
    "measure",
    "move",
    "name",
    "parts",
    "placement",
    "resolve",
    "spin",
    "view",
    "write",
]
