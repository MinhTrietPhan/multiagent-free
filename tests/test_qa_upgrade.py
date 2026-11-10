import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from langchain_core.messages import AIMessage, HumanMessage
from hub.agents.qa.node import qa_node, _history, _load_context_with_path
import time, os

def mk(tmp, root_text=None):
    ctx = f'# Project context: P\n> Root: `{root_text or tmp}`\n## Files\n'
    (tmp / 'PROJECT_CONTEXT_map.md').write_text(ctx, encoding='utf-8')

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
    texts = [m.content for m in sent]
    assert any('hàm train là gì?' in t for t in texts)
    assert any(t == 'Là `train()` ở model.py' for t in texts)          # footer đã bị bỏ
    assert not any('Đã tạo file' in t for t in texts)                    # agent khác không lẫn vào
    assert texts[-1] == 'Câu hỏi: còn hàm kia?'
    assert out['messages'][0].name == 'qa' and 'PROJECT_CONTEXT_map.md' in out['messages'][0].content

def test_history_limit(tmp_path):
    msgs = []
    for i in range(6):
        msgs += [HumanMessage(f'q{i}'), AIMessage(f'a{i}', name='qa')]
    msgs.append(HumanMessage('now'))
    h = _history({'messages': msgs}, max_turns=2)
    assert [m.content for m in h] == ['Câu hỏi: q4', 'a4', 'Câu hỏi: q5', 'a5']

def test_newest_context_file_wins(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'PROJECT_CONTEXT.md').write_text('old'); time.sleep(0.05)
    (tmp_path / 'PROJECT_CONTEXT_full.md').write_text('new')
    assert _load_context_with_path()[1].name == 'PROJECT_CONTEXT_full.md'

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

def test_no_context(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert 'Chưa có PROJECT_CONTEXT.md' in qa_node({'messages': [HumanMessage('x?')]})['messages'][0].content
