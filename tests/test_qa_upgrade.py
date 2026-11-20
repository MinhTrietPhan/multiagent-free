import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage
from hub.agents.qa.node import _history, qa_node
import time, os

def mk(tmp, root_text=None):
    """Minimal project marker; QA builds its own index, no context file needed."""
    (tmp / 'pyproject.toml').write_text("[project]\nname='demo'\n", encoding='utf-8')

def llm_with(*contents):
    m = MagicMock(); m.invoke.side_effect = [MagicMock(content=c) for c in contents]; return m

def test_history_used_and_footer_stripped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); mk(tmp_path)
    llm = llm_with('ok')
    state = {'messages': [HumanMessage('hàm train là gì?'),
                          AIMessage('Là `train()` ở model.py\n\n---\n📄 Nguồn: `x`', name='qa'),
                          HumanMessage('đã tạo context'), AIMessage('Đã tạo file', name=None),
                          HumanMessage('còn hàm kia?')]}
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        out = qa_node(state)
    sent = llm.invoke.call_args[0][0]
    blob = '\n'.join(m.content for m in sent)
    assert 'hàm train là gì?' in blob                      # lượt cũ được nhớ
    assert 'Là `train()` ở model.py' in blob               # footer đã bị bỏ
    assert '📄 Nguồn' not in blob
    assert '[hub] Đã tạo file' in blob                     # lượt của agent khác cũng vào history, có nhãn
    assert sent[-1].content == 'Câu hỏi: còn hàm kia?'
    assert out['messages'][0].name == 'qa' and '🗂️' in out['messages'][0].content

def test_history_limit(tmp_path):
    msgs = []
    for i in range(6):
        msgs += [HumanMessage(f'q{i}'), AIMessage(f'a{i}', name='qa')]
    msgs.append(HumanMessage('now'))
    h = _history({'messages': msgs}, max_turns=2)
    text = '\n'.join(m.content for m in h)
    assert 'q4' in text and 'q5' in text
    assert 'q3' not in text                                 # ngoài cửa sổ
    assert '4 lượt cũ hơn đã lược bớt' in text

def test_project_context_files_are_not_auto_loaded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); mk(tmp_path)
    (tmp_path / 'PROJECT_CONTEXT_full.md').write_text('STALE_MARKER_42', encoding='utf-8')
    (tmp_path / 'app.py').write_text('VALUE = 1\n', encoding='utf-8')
    llm = llm_with('ok')
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        qa_node({'messages': [HumanMessage('hỏi')]})
    sent = '\n'.join(m.content for m in llm.invoke.call_args[0][0])
    assert 'STALE_MARKER_42' not in sent
    assert 'app.py' in sent

def test_read_range_and_js_function(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); mk(tmp_path)
    (tmp_path / 'a.js').write_text('function greet(n) {\n  return n;\n}\n' + '\n'.join(f'// {i}' for i in range(900)))
    llm = llm_with(json.dumps({'need_file': 'a.js', 'function': 'greet'}),
                   json.dumps({'need_file': 'a.js', 'start': 600, 'end': 610}), 'xong')
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        out = qa_node({'messages': [HumanMessage('hàm greet')]})
    msg = out['messages'][0].content
    assert llm.invoke.call_count == 3 and msg.startswith('xong')
    assert 'a.js::greet' in msg and 'a.js:L600-610' in msg
    allmsg = '\n'.join(m.content for m in llm.invoke.call_args[0][0])
    assert 'L1: function greet(n) {' in allmsg and 'L3: }' in allmsg
    assert 'L600:' in allmsg and 'L610:' in allmsg and 'L611:' not in allmsg

def test_path_escape_blocked(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); mk(tmp_path)
    (tmp_path.parent / 'secret.txt').write_text('TOPSECRET')
    llm = llm_with(json.dumps({'need_file': '../secret.txt'}), 'không đọc được')
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        out = qa_node({'messages': [HumanMessage('đọc secret')]})
    assert all('TOPSECRET' not in m.content for call in llm.invoke.call_args_list for m in call[0][0])

def test_budget_exhausted_forces_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path); mk(tmp_path)
    (tmp_path / 'f.py').write_text('x=1\n')
    need = json.dumps({'need_file': 'f.py'})
    llm = llm_with(need, need, need, 'trả lời cuối')
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        out = qa_node({'messages': [HumanMessage('hỏi')]})
    assert llm.invoke.call_count == 4 and out['messages'][0].content.startswith('trả lời cuối')

def test_user_path_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = qa_node({'messages': [HumanMessage('đọc "C:\\Users\\X\\Desktop\\PROJECT_CONTEXT.md"')]})
    assert 'Không tìm thấy file' in out['messages'][0].content and 'OneDrive' in out['messages'][0].content

def test_user_path_found(tmp_path, monkeypatch):
    other = tmp_path / 'elsewhere'; other.mkdir(); work = tmp_path / 'work'; work.mkdir()
    f = other / 'ctx.md'; f.write_text('# Project context: Z\n')
    monkeypatch.chdir(work)
    llm = llm_with('ok')
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        out = qa_node({'messages': [HumanMessage(f'đọc "{f}" rồi tóm tắt')]})
    sent = ' '.join(m.content for m in llm.invoke.call_args[0][0])
    assert str(f) not in sent.split('Câu hỏi:')[-1] and 'đã được nạp sẵn' in sent and str(f) in out['messages'][0].content

def test_no_context_still_uses_source(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'pyproject.toml').write_text("[project]\nname='sample'\n", encoding='utf-8')
    (tmp_path / 'source.py').write_text('VALUE = 42\n', encoding='utf-8')
    llm = llm_with('đã đọc source')
    with patch('hub.agents.qa.node.get_llm', return_value=llm):
        out = qa_node({'messages': [HumanMessage('giá trị VALUE là gì?')]})
    assert out['messages'][0].content.startswith('đã đọc source')
    assert '🗂️' in out['messages'][0].content
    assert 'Không có PROJECT_CONTEXT' not in out['messages'][0].content


def test_initial_index_keeps_only_navigation_data(tmp_path, monkeypatch):
    """The prompt carries the index, never full code bodies."""
    from hub.mini_map import build_mini_map

    (tmp_path / 'pyproject.toml').write_text("[project]\nname='demo'\n", encoding='utf-8')
    (tmp_path / 'main.py').write_text('SECRET_FULL_BODY = 123\n', encoding='utf-8')
    text = build_mini_map(tmp_path)
    assert 'main.py' in text
    assert 'SECRET_FULL_BODY' not in text
