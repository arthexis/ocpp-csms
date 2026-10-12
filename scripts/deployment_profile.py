"""Durable nonsecret deployment intent for the local CSMS appliance.

Transient maintenance switches and arbitrary Ansible extra variables are never stored.
All profiles are validated before rendering any shell assignments.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import sys
import tempfile

DOMAIN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
INTERFACE = re.compile(r"^[A-Za-z0-9_.:-]+$")
FIELDS = {"mode", "expose", "email", "no_certbot", "forwarder", "interface"}
DEFAULT = dict(mode="satellite", expose="", email="", no_certbot=False, forwarder=False, interface="")
SCHEMA = 1


def validate(profile: dict) -> dict:
    if not isinstance(profile, dict) or set(profile) != FIELDS | {"schema"}:
        raise ValueError("Invalid deployment profile keys")
    if type(profile["schema"]) is not int or profile["schema"] != SCHEMA:
        raise ValueError("Unsupported deployment profile version")
    if profile["mode"] not in ("satellite", "delegate"):
        raise ValueError("Invalid installed mode")
    if any(type(profile[k]) is not bool for k in ("forwarder", "no_certbot")):
        raise ValueError("Invalid boolean deployment settings")
    for k in ("expose", "email", "interface"):
        if not isinstance(profile[k], str):
            raise ValueError("Invalid deployment setting: " + k)
    if profile["expose"] and (not DOMAIN.fullmatch(profile["expose"]) or "." not in profile["expose"]):
        raise ValueError("Invalid expose hostname")
    if profile["email"] and not EMAIL.fullmatch(profile["email"]):
        raise ValueError("Invalid Certbot email")
    if profile["interface"] and not INTERFACE.fullmatch(profile["interface"]):
        raise ValueError("Invalid interface name")
    if profile["mode"] == "satellite" and (profile["expose"] or profile["email"] or profile["no_certbot"]):
        raise ValueError("Satellite cannot have nginx public exposure")
    return profile


def read(path: Path):
    if not path.exists():
        return None
    if path.is_symlink():
        raise ValueError("Deployment profile cannot be a symlink")
    return validate(json.loads(path.read_text(encoding="utf-8")))


def resolve(previous, args):
    if previous is None:
        config = dict(DEFAULT)
    else:
        config = {key: previous[key] for key in FIELDS}
    requested_mode = "satellite" if args.satellite else "delegate" if args.delegate or args.expose else None
    if previous and requested_mode and previous["mode"] != requested_mode:
        raise ValueError("Installed " + previous["mode"] + " cannot switch to " + requested_mode + "; uninstall first")
    if requested_mode:
        config["mode"] = requested_mode
    if args.expose:
        if config["expose"] and config["expose"] != args.expose:
            raise ValueError("Public hostname cannot be changed without uninstall")
        config["expose"] = args.expose
    if args.email:
        config["email"] = args.email
    if args.no_certbot:
        config["no_certbot"] = True
    if args.forwarder:
        config["forwarder"] = True
    if args.no_forwarder:
        config["forwarder"] = False
    if args.interface:
        config["interface"] = args.interface
    validate({"schema": SCHEMA, **config})
    return config


def render_shell(config):
    for key in sorted(FIELDS):
        val = config[key]
        if isinstance(val, bool):
            val = "1" if val else "0"
        print("profile_" + key + "=" + shlex.quote(str(val)))


def write(path: Path, config):
    data = json.dumps(validate({"schema": SCHEMA, **config}), sort_keys=True, indent=2) + "\n"
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".deployment-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("resolve", "commit", "clear"))
    parser.add_argument("--path", required=True, type=Path)
    parser.add_argument("--satellite", type=int, choices=(0, 1), default=0)
    parser.add_argument("--delegate", type=int, choices=(0, 1), default=0)
    parser.add_argument("--expose", default="")
    parser.add_argument("--email", default="")
    parser.add_argument("--no-certbot", type=int, choices=(0, 1), default=0)
    parser.add_argument("--forwarder", type=int, choices=(0, 1), default=0)
    parser.add_argument("--no-forwarder", type=int, choices=(0, 1), default=0)
    parser.add_argument("--interface", default="")
    parser.add_argument("--adopt", type=int, choices=(0, 1), default=0)
    args = parser.parse_args()
    try:
        if args.operation == "clear":
            args.path.unlink(missing_ok=True)
            return 0
        previous = read(args.path)
        if previous is None and Path("/etc/systemd/system/ocpp-csms.service").exists():
            if not args.adopt or not (args.satellite or args.delegate or args.expose):
                raise ValueError("Existing untracked CSMS installation; use --adopt with its verified mode")
        if args.satellite and (args.delegate or args.expose):
            raise ValueError("--satellite cannot be combined with delegate or expose")
        if args.delegate and args.expose:
            raise ValueError("--delegate and --expose are alternative mode selectors")
        if args.forwarder and args.no_forwarder:
            raise ValueError("--forwarder and --no-forwarder conflict")
        config = resolve(previous, args)
        if args.operation == "resolve":
            render_shell(config)
        else:
            write(args.path, config)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print("Deployment profile: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
