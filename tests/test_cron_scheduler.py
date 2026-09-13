import json
import threading
from datetime import datetime

import pytest

import mini_claude_code.cron_scheduler as cron_scheduler
import mini_claude_code.security as security


class FakeSession:
    def __init__(self, session_id):
        self.id = session_id
        self.messages = []


@pytest.fixture
def detached_cron_runtime(monkeypatch):
    """Keep module-level Cron wiring and thread state out of the other tests."""
    monkeypatch.setattr(cron_scheduler, '_agent_runner', None)
    monkeypatch.setattr(cron_scheduler, '_session_resolver', None)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    monkeypatch.setattr(cron_scheduler, 'cron_queue', [])
    monkeypatch.setattr(cron_scheduler, 'delivery_failures', {})
    yield
    # process_cron_batch 会把本线程标记为非交互。
    security.set_execution_context()


def test_cron_expression_validation_and_matching():
    assert cron_scheduler.validate_cron('*/5 9-17 * * 1-5') is None
    assert cron_scheduler.validate_cron('61 * * * *') is not None
    assert cron_scheduler.cron_matches(
        '*/5 9-17 * * 1-5',
        datetime(2026, 8, 26, 10, 15),
    ) is True


def test_find_next_run_keeps_exact_scheduled_minute():
    created_at = datetime(2026, 8, 28, 9, 0, 30)

    assert cron_scheduler.find_next_run(
        '30 9 28 8 *',
        created_at,
    ) == datetime(2026, 8, 28, 9, 30)


def test_durable_job_round_trip(tmp_path, monkeypatch):
    storage = tmp_path / '.scheduled_tasks.json'
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    monkeypatch.setattr(cron_scheduler.random, 'randint', lambda start, end: 123456)

    job = cron_scheduler.schedule_job('* * * * *', 'run checks', durable=True)
    assert storage.exists()

    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    cron_scheduler.load_durable_jobs()

    assert cron_scheduler.scheduled_jobs[job.id].prompt == 'run checks'


def test_one_time_job_persists_its_exact_run_time(tmp_path, monkeypatch):
    storage = tmp_path / '.scheduled_tasks.json'
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    monkeypatch.setattr(cron_scheduler.random, 'randint', lambda start, end: 654321)

    job = cron_scheduler.schedule_job(
        '30 9 28 8 *',
        'interview',
        recurring=False,
        durable=True,
    )

    assert not isinstance(job, str)
    assert job.run_at is not None

    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    cron_scheduler.load_durable_jobs()

    restored = cron_scheduler.scheduled_jobs[job.id]
    assert restored.run_at == job.run_at


def test_cron_runtime_receives_runner_and_session_resolver(detached_cron_runtime):
    def fake_agent_runner(session):
        return session

    def fake_resolver(session_id):
        return FakeSession('session-current')

    cron_scheduler.configure_cron_runtime(fake_agent_runner, fake_resolver)

    assert cron_scheduler._agent_runner is fake_agent_runner
    assert cron_scheduler._session_resolver is fake_resolver
    assert cron_scheduler.owning_session_id() == 'session-current'


def test_a_running_turn_owns_the_jobs_it_creates(detached_cron_runtime):
    """A Cron turn on a non-current session must own its new jobs."""
    running = FakeSession('session-running')
    cron_scheduler.configure_cron_runtime(
        lambda session: None,
        lambda session_id: FakeSession('session-current'),
    )

    assert cron_scheduler.owning_session_id() == 'session-current'

    with cron_scheduler.turn_session(running):
        assert cron_scheduler.owning_session_id() == 'session-running'

    assert cron_scheduler.owning_session_id() == 'session-current'


def test_schedule_list_and_cancel_wrappers(tmp_path, monkeypatch):
    storage = tmp_path / '.scheduled_tasks.json'
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    monkeypatch.setattr(cron_scheduler.random, 'randint', lambda start, end: 777)

    created = cron_scheduler.run_schedule_cron(
        '0 12 * * *', 'lunch', recurring=True, durable=True
    )
    assert 'cron_000777' in created
    assert 'lunch' in cron_scheduler.run_list_crons()
    assert '已取消' in cron_scheduler.run_cancel_cron('cron_000777')
    assert cron_scheduler.run_list_crons() == '当前没有定时任务'


def test_cron_validation_covers_lists_ranges_and_errors():
    assert cron_scheduler.validate_cron('0,30 9-17 * * 1-5') is None
    assert cron_scheduler.validate_cron('*/0 * * * *') is not None
    assert cron_scheduler.validate_cron('0 18-9 * * *') is not None
    assert cron_scheduler.validate_cron('not-a-cron') is not None
    assert cron_scheduler.cron_field_matches('1,2,3', 2)
    assert not cron_scheduler.cron_field_matches('bad', 2)


def test_job_keeps_its_owner_session_after_a_switch(
    tmp_path,
    monkeypatch,
    detached_cron_runtime,
):
    storage = tmp_path / '.scheduled_tasks.json'
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler.random, 'randint', lambda start, end: 1)

    owner = FakeSession('session-owner')
    switched = FakeSession('session-switched')
    current = {'session': owner}
    known = {owner.id: owner, switched.id: switched}
    ran = []

    def resolver(session_id):
        if session_id == '':
            return current['session']
        return known.get(session_id)

    cron_scheduler.configure_cron_runtime(ran.append, resolver)

    job = cron_scheduler.schedule_job('* * * * *', 'nightly report')
    assert job.session_id == owner.id

    # 任务到期前用户切换了会话。
    current['session'] = switched

    cron_scheduler.deliver_fired_jobs([job])

    assert ran == [owner]
    assert owner.messages[-1]['content'].endswith('nightly report')
    assert switched.messages == []


def test_delivery_skips_jobs_whose_session_is_gone(
    monkeypatch,
    detached_cron_runtime,
    capsys,
):
    ran = []
    cron_scheduler.configure_cron_runtime(ran.append, lambda session_id: None)

    job = cron_scheduler.CronJob(
        id='cron_gone',
        cron='* * * * *',
        prompt='report',
        recurring=False,
        durable=False,
        session_id='session-deleted',
    )

    assert cron_scheduler.deliver_fired_jobs([job]) == []
    assert ran == []
    assert 'session-deleted' in capsys.readouterr().out


def make_job(job_id='cron_1', prompt='report', session_id=''):
    return cron_scheduler.CronJob(
        id=job_id,
        cron='* * * * *',
        prompt=prompt,
        recurring=True,
        durable=False,
        session_id=session_id,
    )


def test_batch_delivery_marks_the_thread_non_interactive(detached_cron_runtime):
    session = FakeSession('session-a')
    observed = {}

    def runner(delivered):
        observed['context'] = security.current_execution_context()

    cron_scheduler.configure_cron_runtime(runner, lambda session_id: session)
    cron_scheduler.cron_queue.append(make_job())

    assert cron_scheduler.process_cron_batch() == [session]
    assert observed['context']['interactive'] is False
    assert observed['context']['label'] == 'cron'


def test_failed_delivery_requeues_then_gives_up(detached_cron_runtime, capsys):
    session = FakeSession('session-a')
    attempts = {'count': 0}

    def failing_runner(delivered):
        attempts['count'] += 1
        raise OSError('会话文件被占用')

    cron_scheduler.configure_cron_runtime(
        failing_runner,
        lambda session_id: session,
    )
    cron_scheduler.cron_queue.append(make_job())

    for _retry in range(cron_scheduler.MAX_DELIVERY_ATTEMPTS - 1):
        assert cron_scheduler.process_cron_batch() == []
        # 任务不能因为一次失败就消失。
        assert len(cron_scheduler.cron_queue) == 1

    assert cron_scheduler.process_cron_batch() == []
    assert cron_scheduler.cron_queue == []
    assert attempts['count'] == cron_scheduler.MAX_DELIVERY_ATTEMPTS

    output = capsys.readouterr().out
    assert '退回队列等待重试' in output
    assert '已放弃' in output


def test_unconfigured_runtime_keeps_the_job_instead_of_dropping_it(
    detached_cron_runtime,
):
    """A batch is already off the queue; losing it silently is the worst outcome."""
    cron_scheduler.cron_queue.append(make_job())

    assert cron_scheduler.process_cron_batch() == []
    assert len(cron_scheduler.cron_queue) == 1


def test_a_successful_delivery_clears_earlier_failures(detached_cron_runtime):
    session = FakeSession('session-a')
    outcomes = {'fail': True}

    def runner(delivered):
        if outcomes['fail']:
            raise OSError('临时失败')

    cron_scheduler.configure_cron_runtime(runner, lambda session_id: session)
    cron_scheduler.cron_queue.append(make_job())

    assert cron_scheduler.process_cron_batch() == []
    assert cron_scheduler.delivery_failures == {'cron_1': 1}

    outcomes['fail'] = False

    assert cron_scheduler.process_cron_batch() == [session]
    assert cron_scheduler.delivery_failures == {}


def test_batch_delivery_skips_work_while_the_agent_is_busy(detached_cron_runtime):
    ran = []
    cron_scheduler.configure_cron_runtime(
        ran.append,
        lambda session_id: FakeSession('session-a'),
    )
    cron_scheduler.cron_queue.append(make_job())

    with cron_scheduler.agent_lock:
        assert cron_scheduler.process_cron_batch() == []

    assert ran == []
    # 抢不到锁时任务留在队列里，下一次轮询再交付。
    assert len(cron_scheduler.cron_queue) == 1


def test_expiring_job_does_not_break_a_concurrent_save(tmp_path, monkeypatch):
    """save_durable_jobs must not iterate a dict another thread can mutate."""
    storage = tmp_path / '.scheduled_tasks.json'
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})
    monkeypatch.setattr(cron_scheduler, 'cron_queue', [])
    monkeypatch.setattr(cron_scheduler, 'last_fired', {})

    for index in range(50):
        job_id = f'cron_{index:03d}'
        cron_scheduler.scheduled_jobs[job_id] = cron_scheduler.CronJob(
            id=job_id,
            cron='* * * * *',
            prompt='report',
            recurring=False,
            durable=True,
            run_at=0.0,
        )

    errors = []

    def keep_saving():
        try:
            for _attempt in range(200):
                cron_scheduler.save_durable_jobs()
        except Exception as error:
            errors.append(error)

    def expire_jobs():
        try:
            for job_id in list(cron_scheduler.scheduled_jobs):
                with cron_scheduler.cron_lock:
                    cron_scheduler.scheduled_jobs.pop(job_id, None)
                    cron_scheduler.save_durable_jobs(
                        cron_scheduler.durable_job_snapshot()
                    )
        except Exception as error:
            errors.append(error)

    threads = [
        threading.Thread(target=keep_saving),
        threading.Thread(target=expire_jobs),
    ]

    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join(timeout=30)

    assert errors == []
    assert json.loads(storage.read_text(encoding='utf-8')) == []


def test_jobs_saved_before_session_binding_still_load(tmp_path, monkeypatch):
    storage = tmp_path / '.scheduled_tasks.json'
    storage.write_text(
        '[{"id":"cron_old","cron":"* * * * *","prompt":"legacy",'
        '"recurring":true,"durable":true}]',
        encoding='utf-8',
    )
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})

    cron_scheduler.load_durable_jobs()

    assert cron_scheduler.scheduled_jobs['cron_old'].session_id == ''


def test_durable_jobs_survive_a_failed_write(tmp_path, monkeypatch):
    storage = tmp_path / '.scheduled_tasks.json'
    storage.write_text('[]', encoding='utf-8')
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {
        'cron_1': cron_scheduler.CronJob(
            id='cron_1',
            cron='* * * * *',
            prompt='report',
            recurring=True,
            durable=True,
        ),
    })

    def explode(path, text, encoding='utf-8'):
        raise OSError('disk full')

    monkeypatch.setattr(cron_scheduler, 'atomic_write_text', explode)

    with pytest.raises(OSError):
        cron_scheduler.save_durable_jobs()

    assert storage.read_text(encoding='utf-8') == '[]'


def test_load_jobs_skips_invalid_entries(tmp_path, monkeypatch):
    storage = tmp_path / '.scheduled_tasks.json'
    storage.write_text(
        '[{"id":"bad","cron":"99 * * * *","prompt":"x",'
        '"recurring":true,"durable":true}]',
        encoding='utf-8',
    )
    monkeypatch.setattr(cron_scheduler, 'SCHEDULED_TASKS_FILE', storage)
    monkeypatch.setattr(cron_scheduler, 'scheduled_jobs', {})

    cron_scheduler.load_durable_jobs()

    assert cron_scheduler.scheduled_jobs == {}
