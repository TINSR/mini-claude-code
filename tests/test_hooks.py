from mini_claude_code.hooks.registry import HOOKS, register_hook, trigger_hooks


def test_hooks_run_in_registration_order_and_stop_on_result() -> None:
    event = "TestEvent"
    calls = []
    HOOKS[event] = []

    def first(value):
        calls.append(("first", value))

    def second(value):
        calls.append(("second", value))
        return "blocked"

    def third(value):
        calls.append(("third", value))

    register_hook(event, first)
    register_hook(event, second)
    register_hook(event, third)

    assert trigger_hooks(event, 42) == "blocked"
    assert calls == [("first", 42), ("second", 42)]

