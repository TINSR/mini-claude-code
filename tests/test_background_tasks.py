import time
from types import SimpleNamespace

import mini_claude_code.background_tasks as background_tasks


def reset_background_state(monkeypatch):
    monkeypatch.setattr(background_tasks, 'background_tasks', {})
    monkeypatch.setattr(background_tasks, 'background_results', {})
    monkeypatch.setattr(background_tasks, 'background_counter', 0)


def test_slow_powershell_is_sent_to_background():
    assert background_tasks.should_run_background(
        'powershell', {'command': 'pytest -q'}
    ) is True
    assert background_tasks.should_run_background(
        'read_file', {'path': 'main.py'}
    ) is False


def test_background_result_becomes_notification(monkeypatch):
    reset_background_state(monkeypatch)
    background_tasks.configure_background_runtime(
        {'powershell': lambda command: f'done:{command}'}
    )
    block = SimpleNamespace(
        id='call-1',
        name='powershell',
        input={'command': 'Get-Date', 'run_in_background': True},
    )

    task_id = background_tasks.start_background_task(block)
    deadline = time.time() + 2
    notifications = []
    while time.time() < deadline and not notifications:
        notifications = background_tasks.collect_background_results()
        time.sleep(0.01)

    assert task_id == 'bg_0001'
    assert len(notifications) == 1
    assert 'done:Get-Date' in notifications[0]
