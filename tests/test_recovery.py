import mini_claude_code.recovery as recovery


def test_recovery_state_uses_configured_primary_model(monkeypatch):
    recovery.configure_recovery('primary-model')
    state = recovery.RecoveryState()
    assert state.current_model == 'primary-model'


def test_retry_returns_after_transient_overload(monkeypatch):
    attempts = iter([RuntimeError('529 overloaded'), 'ok'])
    monkeypatch.setattr(recovery.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(recovery, 'retry_delay', lambda attempt: 0)

    def request():
        value = next(attempts)
        if isinstance(value, Exception):
            raise value
        return value

    result = recovery.call_with_retry(request, recovery.RecoveryState())

    assert result == 'ok'


def test_non_transient_error_is_not_swallowed():
    def fail():
        raise ValueError('invalid input')

    try:
        recovery.call_with_retry(fail, recovery.RecoveryState())
    except ValueError as error:
        assert str(error) == 'invalid input'
    else:
        raise AssertionError('ValueError should be propagated')
