"""Focused regression tests for the Ansible downgrade preflight's systemd parser."""
import ast
import re
from pathlib import Path
import pytest

def _active_schema_pattern():
    tasks = Path(__file__).resolve().parents[1] / "ansible/roles/ocpp_csms/tasks/main.yml"
    line = next(line.strip() for line in tasks.read_text().splitlines() if "versions = re.findall(" in line)
    expression = ast.parse(line).body[0].value
    assert isinstance(expression, ast.Call)
    return re.compile(expression.args[0].value)

@pytest.mark.parametrize(("exec_start", "expected"), [
    ('{ path=/opt/csms ; argv[]=/opt/csms --data-dir /home/runner/ocpp-csms-data/schemas/8 serve ; }', "8"),
    ('{ path=/opt/csms ; argv[]=/opt/csms --data-dir /home/runner/ocpp-csms-data/schemas/9 serve ; }', "9"),
    ('--data-dir=/srv/ocpp-csms/schemas/12/ serve', "12"),
    ('--data-dir /srv/ocpp-csms/schemas/8; serve', "8"),
    ('--data-dir /srv/ocpp-csms/schemas/8x serve', None),
    ('--data-dir /srv/ocpp-csms/data serve', None),
])
def test_systemd_schema_parser(exec_start, expected):
    matches = _active_schema_pattern().findall(exec_start)
    assert matches == ([expected] if expected else [])
