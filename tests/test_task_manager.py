import json

import mini_claude_code.task_manager as task_manager


def test_task_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(task_manager, 'TASKS_DIR', tmp_path)
    monkeypatch.setattr(task_manager.random, 'randint', lambda start, end: 1234)

    task = task_manager.create_task('Inspect code', 'Read main.py')
    loaded = task_manager.load_task(task.id)

    assert loaded == task
    assert json.loads(task_manager.get_task(task.id))['subject'] == 'Inspect code'


def test_dependencies_block_then_unlock_task(tmp_path, monkeypatch):
    monkeypatch.setattr(task_manager, 'TASKS_DIR', tmp_path)

    ids = iter([1001, 1002])
    monkeypatch.setattr(task_manager.random, 'randint', lambda start, end: next(ids))

    dependency = task_manager.create_task('Create file')
    dependent = task_manager.create_task('Review file', blockedBy=[dependency.id])

    assert task_manager.can_start(dependent.id) is False
    assert task_manager.claim_task(dependent.id) == '任务仍被依赖阻塞'

    task_manager.claim_task(dependency.id, owner='alice')
    result = task_manager.complete_task(dependency.id)

    assert 'Review file' in result
    assert task_manager.can_start(dependent.id) is True
    assert '已认领任务' in task_manager.claim_task(dependent.id, owner='bob')
