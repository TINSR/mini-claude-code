"""The one place where a tool call is authorized and then executed.

Every agent path — main loop, subagent, background worker, teammate thread and
Cron delivery — goes through :func:`execute_tool`, so an operation the permission
system refuses stays refused no matter which path requested it.

Whether a thread may prompt the user, and which workspace its relative paths
belong to, is declared per thread through
:func:`mini_claude_code.security.set_execution_context`.
"""

from .hooks import trigger_hooks


class ToolCall:
    """A tool call the runtime issues itself, e.g. a teammate auto-claim.

    Gives such calls the same shape as a model ``tool_use`` block so they can
    go through the same authorization entry point.
    """

    type = 'tool_use'

    def __init__(self, name, tool_input, call_id=''):
        self.name = name
        self.input = tool_input
        self.id = call_id


def authorize_tool(block):
    """Return a denial message, or ``None`` when the call may proceed."""
    blocked = trigger_hooks('PreToolUse', block)

    if blocked is None:
        return None

    return str(blocked)


def invoke_handler(block, handlers):
    handler = handlers.get(block.name)

    if handler is None:
        return f'错误：未知工具 {block.name}'

    try:
        output = handler(**block.input)
    except Exception as error:
        return f'工具执行失败：{type(error).__name__}: {error}'

    trigger_hooks('PostToolUse', block, output)
    return output


def execute_tool(block, handlers):
    """Authorize ``block`` through the Hook pipeline, then run its handler."""
    denial = authorize_tool(block)

    if denial is not None:
        return denial

    return invoke_handler(block, handlers)
