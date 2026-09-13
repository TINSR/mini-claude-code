"""Hook registration and dispatch."""

from .registry import HOOKS, register_hook, trigger_hooks

__all__ = ["HOOKS", "register_hook", "trigger_hooks"]

