from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ANSIBLE = ROOT / "ansible"
ROLE = ANSIBLE / "roles" / "ocpp_csms"
TASKS = ROLE / "tasks"
DEFAULTS = ROLE / "defaults" / "main.yml"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


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


def all_ansible_text() -> str:
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in ANSIBLE.rglob("*")
        if path.is_file()
    )
