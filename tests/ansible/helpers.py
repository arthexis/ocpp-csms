from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"
PLAYBOOK = ANSIBLE / "playbooks" / "satellite.yml"
ROLE = ANSIBLE / "roles" / "ocpp_csms"
TASKS = ROLE / "tasks"
DEFAULTS = ROLE / "defaults" / "main.yml"
SERVICE_TEMPLATE = ROLE / "templates" / "ocpp-csms.service.j2"
DEPLOY_SCRIPT = ROOT / "ansible-deploy.sh"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def load_yaml(path: Path):
    return yaml.safe_load(read(path))


def named_tasks(path: Path) -> list[dict]:
    tasks = load_yaml(path)
    assert isinstance(tasks, list), f"expected Ansible task list: {path}"
    return [task for task in tasks if isinstance(task, dict) and "name" in task]


def task_by_name(path: Path, name: str) -> dict:
    matches = [task for task in named_tasks(path) if task.get("name") == name]
    assert len(matches) == 1, f"expected one Ansible task named {name!r}, found {len(matches)}"
    return matches[0]


def task_names(path: Path) -> list[str]:
    return [str(task["name"]) for task in named_tasks(path)]


def assert_named_task_order(path: Path, *names: str) -> None:
    ordered = task_names(path)
    positions = []
    for name in names:
        assert name in ordered, f"missing Ansible task: {name}"
        positions.append(ordered.index(name))
    assert positions == sorted(positions), f"unexpected task order: {names}"


def task_marker(name: str) -> str:
    return f"- name: {name}"


def task_position(text: str, name: str) -> int:
    marker = task_marker(name)
    assert marker in text, f"missing Ansible task: {name}"
    return text.index(marker)


def task_section(text: str, name: str) -> str:
    start = task_position(text, name)
    next_task = text.find("\n- name: ", start + 1)
    return text[start:] if next_task == -1 else text[start:next_task]


def assert_task_order(text: str, *names: str) -> None:
    positions = [task_position(text, name) for name in names]
    assert positions == sorted(positions), f"unexpected task order: {names}"


def role_text() -> str:
    """Return only the implemented CSMS role, excluding future role scaffolds."""
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in ROLE.rglob("*")
        if path.is_file()
    )
