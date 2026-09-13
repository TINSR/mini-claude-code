import mini_claude_code.prompt_manager as prompt_manager


def test_context_lists_enabled_tools(tmp_path, monkeypatch):
    index = tmp_path / 'MEMORY.md'
    index.write_text('- preferred-style', encoding='utf-8')
    monkeypatch.setattr(prompt_manager, 'MEMORY_INDEX', index)
    prompt_manager.configure_prompt_runtime({'read_file': object(), 'glob': object()})

    context = prompt_manager.update_context()

    assert context['enabled_tools'] == ['read_file', 'glob']
    assert context['memories'] == '- preferred-style'


def test_system_prompt_cache_reuses_same_string(monkeypatch):
    monkeypatch.setattr(prompt_manager, '_last_context_key', None)
    monkeypatch.setattr(prompt_manager, '_last_system_prompt', None)
    context = {'enabled_tools': ['read_file'], 'workspace': 'C:/demo', 'memories': ''}

    first = prompt_manager.get_system_prompt(context)
    second = prompt_manager.get_system_prompt(context)

    assert first == second
    context['memories'] = 'new memory'
    context['enabled_tools'].append('mcp__search')
    assert first == prompt_manager.get_system_prompt(context)
    assert 'C:/demo' in first
