import mini_claude_code.worktree_manager as worktree_manager


def test_worktree_name_validation():
    assert worktree_manager.validate_worktree_name('feature-01') is None
    assert worktree_manager.validate_worktree_name('.') is not None
    assert worktree_manager.validate_worktree_name('../escape') is not None
    assert worktree_manager.validate_worktree_name('contains space') is not None


def test_bind_task_to_worktree(tmp_path, monkeypatch):
    monkeypatch.setattr(worktree_manager, 'WORKTREES_DIR', tmp_path / '.worktrees')

    class FakeTask:
        id = 'task-1'
        worktree = None

    task = FakeTask()
    saved = []
    monkeypatch.setattr(worktree_manager, 'load_task', lambda task_id: task)
    monkeypatch.setattr(worktree_manager, 'save_task', saved.append)

    worktree_manager.bind_task_to_worktree('task-1', 'review')

    assert task.worktree == 'review'
    assert saved == [task]


def test_remove_refuses_dirty_worktree(tmp_path, monkeypatch):
    worktrees = tmp_path / '.worktrees'
    target = worktrees / 'review'
    target.mkdir(parents=True)
    monkeypatch.setattr(worktree_manager, 'WORKTREES_DIR', worktrees)
    monkeypatch.setattr(worktree_manager, 'count_worktree_changes', lambda path: (1, 0))

    result = worktree_manager.remove_worktree('review', discard_changes=False)

    assert result.startswith('拒绝删除')


def test_create_worktree_success_and_task_binding(tmp_path, monkeypatch):
    worktrees = tmp_path / '.worktrees'
    worktrees.mkdir()
    monkeypatch.setattr(worktree_manager, 'WORKTREES_DIR', worktrees)
    monkeypatch.setattr(worktree_manager, 'run_git', lambda arguments: (True, 'ok'))
    monkeypatch.setattr(worktree_manager, 'load_task', lambda task_id: object())
    bound = []
    monkeypatch.setattr(
        worktree_manager,
        'bind_task_to_worktree',
        lambda task_id, name: bound.append((task_id, name)),
    )
    result = worktree_manager.create_worktree('feature', 'task-1')
    assert '创建成功' in result
    assert bound == [('task-1', 'feature')]
    assert (worktrees / 'events.jsonl').exists()


def test_keep_and_remove_clean_worktree(tmp_path, monkeypatch):
    worktrees = tmp_path / '.worktrees'
    target = worktrees / 'review'
    target.mkdir(parents=True)
    monkeypatch.setattr(worktree_manager, 'WORKTREES_DIR', worktrees)
    monkeypatch.setattr(worktree_manager, 'count_worktree_changes', lambda path: (0, 0))
    calls = []
    monkeypatch.setattr(
        worktree_manager,
        'run_git',
        lambda arguments: (calls.append(arguments) or True, 'ok'),
    )
    assert '已保留' in worktree_manager.run_keep_worktree('review')
    assert '已删除' in worktree_manager.run_remove_worktree('review')
    assert calls[0][:2] == ['worktree', 'remove']
    assert calls[1][:2] == ['branch', '-D']


def test_worktree_failure_paths(tmp_path, monkeypatch):
    worktrees = tmp_path / '.worktrees'
    worktrees.mkdir()
    monkeypatch.setattr(worktree_manager, 'WORKTREES_DIR', worktrees)
    monkeypatch.setattr(
        worktree_manager,
        'load_task',
        lambda task_id: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert '名称不合法' in worktree_manager.create_worktree('..')
    assert '找不到任务' in worktree_manager.create_worktree('valid', 'missing')
    assert '找不到' in worktree_manager.keep_worktree('missing')
    assert '找不到' in worktree_manager.remove_worktree('missing')
