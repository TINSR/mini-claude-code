from pathlib import Path

import mini_claude_code.memory_manager as memory_manager


class FakeMessagesAPI:
    def __init__(self, responses):
        self.responses = iter(responses)

    def create(self, **kwargs):
        text = next(self.responses)
        return type('Response', (), {'content': text})()


class FakeClient:
    def __init__(self, responses):
        self.messages = FakeMessagesAPI(responses)


def test_memory_file_round_trip(tmp_path, monkeypatch):
    memory_dir = tmp_path / '.memory'
    memory_dir.mkdir()

    monkeypatch.setattr(memory_manager, 'MEMORY_DIR', memory_dir)
    monkeypatch.setattr(memory_manager, 'MEMORY_INDEX', memory_dir / 'MEMORY.md')

    path = memory_manager.write_memory_file(
        'Python Style',
        'user',
        'Preferred quote style',
        'Use single quotes.',
    )

    assert path == Path(memory_dir / 'python-style.md')
    assert memory_manager.read_memory_file(path.name) is not None

    memories = memory_manager.list_memory_files()
    assert len(memories) == 1
    assert memories[0]['name'] == 'Python Style'
    assert memories[0]['body'] == 'Use single quotes.'


def test_load_memories_wraps_selected_files(tmp_path, monkeypatch):
    memory_dir = tmp_path / '.memory'
    memory_dir.mkdir()
    memory_file = memory_dir / 'preference.md'
    memory_file.write_text('Remember this.', encoding='utf-8')

    monkeypatch.setattr(memory_manager, 'MEMORY_DIR', memory_dir)
    monkeypatch.setattr(
        memory_manager,
        'select_relevant_memories',
        lambda messages: ['preference.md'],
    )

    result = memory_manager.load_memories([{'role': 'user', 'content': 'hello'}])

    assert result.startswith('<relevant_memories>')
    assert 'Remember this.' in result
    assert result.endswith('</relevant_memories>')


def test_select_relevant_memories_uses_model_indices(tmp_path, monkeypatch):
    memory_dir = tmp_path / '.memory'
    memory_dir.mkdir()
    monkeypatch.setattr(memory_manager, 'MEMORY_DIR', memory_dir)
    monkeypatch.setattr(memory_manager, 'MEMORY_INDEX', memory_dir / 'MEMORY.md')
    memory_manager.write_memory_file('Style', 'user', 'quotes', 'Use single quotes.')
    memory_manager.configure_memory_runtime(
        FakeClient(['selection: [0]']),
        'fake-model',
        lambda content: content,
    )

    selected = memory_manager.select_relevant_memories([
        {'role': 'user', 'content': 'How should strings be written?'},
    ])

    assert selected == ['style.md']


def test_extract_memories_writes_valid_items(tmp_path, monkeypatch):
    memory_dir = tmp_path / '.memory'
    memory_dir.mkdir()
    monkeypatch.setattr(memory_manager, 'MEMORY_DIR', memory_dir)
    monkeypatch.setattr(memory_manager, 'MEMORY_INDEX', memory_dir / 'MEMORY.md')
    payload = (
        '[{"name":"Quote Style","type":"invalid",'
        '"description":"Python preference","body":"Use single quotes."}]'
    )
    memory_manager.configure_memory_runtime(
        FakeClient([payload]),
        'fake-model',
        lambda content: content,
    )

    saved = memory_manager.extract_memories([
        {'role': 'user', 'content': 'Remember my quote style.'},
    ])

    assert saved == 1
    assert memory_manager.list_memory_files()[0]['type'] == 'user'


def test_consolidate_memories_replaces_old_files(tmp_path, monkeypatch):
    memory_dir = tmp_path / '.memory'
    memory_dir.mkdir()
    monkeypatch.setattr(memory_manager, 'MEMORY_DIR', memory_dir)
    monkeypatch.setattr(memory_manager, 'MEMORY_INDEX', memory_dir / 'MEMORY.md')
    monkeypatch.setattr(memory_manager, 'CONSOLIDATE_MARKER', memory_dir / '.marker')
    monkeypatch.setattr(memory_manager, 'CONSOLIDATE_THRESHOLD', 2)
    memory_manager.write_memory_file('Old One', 'user', 'old', 'first')
    memory_manager.write_memory_file('Old Two', 'project', 'old', 'second')
    payload = (
        '[{"name":"Merged","type":"user",'
        '"description":"merged memory","body":"combined"}]'
    )
    memory_manager.configure_memory_runtime(
        FakeClient([payload]),
        'fake-model',
        lambda content: content,
    )

    memory_manager.consolidate_memories()

    memories = memory_manager.list_memory_files()
    assert [item['name'] for item in memories] == ['Merged']
    assert (memory_dir / '.marker').exists()
