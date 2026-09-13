import mini_claude_code.team_runtime as team_runtime


def test_message_bus_delivers_and_consumes_messages(tmp_path):
    bus = team_runtime.MessageBus(tmp_path)
    bus.send('alice', 'lead', 'review complete')

    messages = bus.read_inbox('lead')

    assert len(messages) == 1
    assert messages[0]['from'] == 'alice'
    assert messages[0]['content'] == 'review complete'
    assert bus.read_inbox('lead') == []


def test_shutdown_response_updates_protocol_state(monkeypatch):
    state = team_runtime.ProtocolState(
        request_id='req_1',
        type='shutdown',
        sender='lead',
        target='alice',
        status='pending',
        payload='',
    )
    monkeypatch.setattr(team_runtime, 'pending_requests', {'req_1': state})

    team_runtime.match_response('shutdown_response', 'req_1', True)

    assert state.status == 'approved'


def test_team_runtime_configuration_keeps_shared_tool_list():
    tools = [{'name': 'read_file'}]
    team_runtime.configure_team_runtime(object(), 'model', tools, str)
    tools.append({'name': 'glob'})

    assert team_runtime._tools[-1]['name'] == 'glob'


def test_send_and_check_inbox_wrappers(tmp_path, monkeypatch):
    bus = team_runtime.MessageBus(tmp_path)
    monkeypatch.setattr(team_runtime, 'BUS', bus)
    assert team_runtime.run_send_message('alice', 'hello') == '消息已发送给 alice'
    bus.send('alice', 'lead', 'done')
    assert team_runtime.run_check_inbox() == '[alice] done'
    assert team_runtime.run_check_inbox() == '收件箱为空'


def test_shutdown_request_and_response_flow(tmp_path, monkeypatch):
    bus = team_runtime.MessageBus(tmp_path)
    monkeypatch.setattr(team_runtime, 'BUS', bus)
    monkeypatch.setattr(team_runtime, 'active_teammates', {'alice': object()})
    monkeypatch.setattr(team_runtime, 'pending_requests', {})
    monkeypatch.setattr(team_runtime.random, 'randint', lambda start, end: 42)
    result = team_runtime.run_request_shutdown('alice')
    request_id = 'req_000042'
    assert request_id in result
    assert bus.read_inbox('alice')[0]['type'] == 'shutdown_request'
    bus.send('alice', 'lead', 'approved', 'shutdown_response', {
        'request_id': request_id,
        'approve': True,
    })
    team_runtime.consume_lead_inbox()
    assert team_runtime.pending_requests[request_id].status == 'approved'


def test_plan_request_and_review_flow(tmp_path, monkeypatch):
    bus = team_runtime.MessageBus(tmp_path)
    monkeypatch.setattr(team_runtime, 'BUS', bus)
    monkeypatch.setattr(team_runtime, 'active_teammates', {'alice': object()})
    state = team_runtime.ProtocolState(
        request_id='req-plan', type='plan_approval', sender='alice',
        target='lead', status='pending', payload='plan',
    )
    monkeypatch.setattr(team_runtime, 'pending_requests', {'req-plan': state})
    assert '提交计划' in team_runtime.run_request_plan('alice', 'review code')
    assert bus.read_inbox('alice')[0]['content'].endswith('review code')
    assert team_runtime.run_review_plan('req-plan', True) == '计划已批准：req-plan'
    assert bus.read_inbox('alice')[0]['metadata']['approve'] is True


def test_protocol_rejects_invalid_requests(monkeypatch):
    monkeypatch.setattr(team_runtime, 'active_teammates', {})
    monkeypatch.setattr(team_runtime, 'pending_requests', {})
    assert '当前没有运行' in team_runtime.run_request_shutdown('missing')
    assert '当前没有运行' in team_runtime.run_request_plan('missing', 'task')
    assert '没有找到' in team_runtime.run_review_plan('missing', True)
