"""Canonical node identity for new CSMS evidence stores.

Explicit configuration overrides the operating system hostname. Existing
database identities are immutable unless converted by an explicit migration.
"""
import re
import socket
import tomllib
from pathlib import Path

NODE_CONFIG = Path("/etc/ocpp-csms/node.toml")
_VALID_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z")


def configured_node_id(config: Path = NODE_CONFIG) -> str:
    if config.exists():
        with config.open("rb") as stream:
            document = tomllib.load(stream)
        if set(document) != {"node"} or not isinstance(document["node"], dict):
            raise ValueError("Node configuration must contain only [node]")
        node = document["node"]
        if set(node) != {"id"} or not isinstance(node["id"], str):
            raise ValueError("[node] must contain a string id")
        value = node["id"]
    else:
        value = socket.gethostname()
    if not _VALID_ID.fullmatch(value):
        raise ValueError("Node ID must be 1-128 letters, digits, '.', '-' or '_' and start with an alphanumeric")
    return value
