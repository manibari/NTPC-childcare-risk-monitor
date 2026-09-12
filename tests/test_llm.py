"""Provider parity at the actual SDK HTTP boundary; no live credentials required."""
import json
import sqlite3

import httpx
import pytest


def config(provider="bedrock_openai", **extra):
    from app.llm import LLMConfig
    return LLMConfig.from_env({"LLM_PROVIDER": provider, "LLM_MODEL": "test-model",
                               "OPENAI_API_KEY": "bedrock-test", "ANTHROPIC_API_KEY": "claude-test",
                               "OPENAI_BASE_URL": "https://api.anthropic.com/v1" if provider == "anthropic" else "",
                               **extra})


def test_provider_credentials_and_defaults():
    c = config()
    assert c.base_url == "https://bedrock-mantle.us-west-2.api.aws/openai/v1"
    assert c.api_key == "bedrock-test"
    assert config(OPENAI_API_KEY="").api_key == "claude-test"
    assert config("anthropic").api_key == "claude-test"
    assert "bedrock-test" not in repr(c)


@pytest.mark.parametrize("extra", [
    {"LLM_PROVIDER": "unknown"}, {"LLM_MODEL": ""},
    {"OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""},
    {"AWS_DEFAULT_REGION": "us-east-1"},
    {"OPENAI_BASE_URL": "https://api.anthropic.com/v1"},
])
def test_invalid_config(extra):
    with pytest.raises(ValueError):
        config(**extra)


def test_anthropic_requires_explicit_endpoint_and_own_key():
    with pytest.raises(ValueError):
        config("anthropic", OPENAI_BASE_URL="")
    with pytest.raises(ValueError):
        config("anthropic", ANTHROPIC_API_KEY="")


@pytest.mark.parametrize("provider", ["anthropic", "bedrock_openai"])
def test_tool_round_trip(provider, tmp_path):
    from app.agent import AgentService
    from app.llm import create_client
    db = tmp_path / "agent.sqlite"
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE app_agent_turns(session_id, page, question, answer, tool_calls, latency_ms, created_at)')
        con.execute('CREATE VIEW v_example AS SELECT 42 AS total')
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers['authorization'] == 'Bearer ' + config(provider).api_key
        assert str(request.url) == config(provider).base_url + '/chat/completions'
        assert body['model'] == 'test-model'
        assert body['max_completion_tokens'] == 1200
        assert 'max_tokens' not in body
        assert body['messages'][0]['role'] == 'system'
        assert body['tools'][0]['type'] == 'function'
        if len(requests) == 1:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'call_1', 'type': 'function', 'function': {'name': 'sql_readonly', 'arguments': '{"sql":"SELECT total FROM v_example"}'}}]}
            reason = 'tool_calls'
        else:
            tool = body['messages'][-1]
            assert tool['role'] == 'tool' and tool['tool_call_id'] == 'call_1'
            assert json.loads(tool['content'])['rows'] == [{'total': 42}]
            message, reason = {'role': 'assistant', 'content': '共 42，來源 v_example。'}, 'stop'
        return httpx.Response(200, json={'id': 'chat_test', 'object': 'chat.completion', 'created': 0,
            'model': 'test-model', 'choices': [{'index': 0, 'message': message, 'finish_reason': reason}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        client = create_client(config(provider), http_client=http)
        service = AgentService(db, config=config(provider), client=client)
        result = service.ask('有多少？')
    assert len(requests) == 2
    assert result['answer'] == '共 42，來源 v_example。'
    assert result['tool_calls'][0]['n'] == 1
    with sqlite3.connect(db) as con:
        assert con.execute('SELECT answer FROM app_agent_turns').fetchone()[0] == result['answer']


def test_missing_config_disables_agent(monkeypatch, tmp_path):
    from app.agent import AgentService
    monkeypatch.setenv('LLM_PROVIDER', '')
    service = AgentService(tmp_path / 'missing.sqlite')
    assert not service.enabled
    assert service.ask('hello')['error'] == 'AGENT_DISABLED'


def test_api_errors_do_not_expose_upstream_secrets(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import main
    from app.agent import AgentService
    service = AgentService(tmp_path / 'unused.sqlite', config=config())

    def fail(*args):
        raise RuntimeError('upstream returned secret-bedrock-key')

    monkeypatch.setattr(service, 'ask', fail)
    monkeypatch.setattr(main, 'agent', service)
    with TestClient(main.app) as api:
        response = api.post('/api/v1/ask', json={'question': 'hello'})
        assert response.status_code == 503
        assert 'secret-bedrock-key' not in response.text
        service.config = None
        service.config_error = '未設定 LLM_MODEL'
        response = api.post('/api/v1/ask', json={'question': 'hello'})
        assert response.status_code == 409
        assert 'LLM_MODEL' in response.text


@pytest.mark.parametrize('arguments', ['not-json', '[]', '{"unexpected":1}'])
def test_malformed_tool_arguments_are_returned_as_tool_errors(arguments, tmp_path):
    from app.agent import AgentService
    from app.llm import create_client
    db = tmp_path / 'agent.sqlite'
    with sqlite3.connect(db) as con:
        con.execute('CREATE TABLE app_agent_turns(session_id, page, question, answer, tool_calls, latency_ms, created_at)')
    requests = []

    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [
                {'id': 'bad_call', 'type': 'function', 'function': {'name': 'sql_readonly', 'arguments': arguments}}]}
        else:
            assert 'error' in json.loads(body['messages'][-1]['content'])
            message = {'role': 'assistant', 'content': '請重新描述問題。'}
        return httpx.Response(200, json={'id': 'test', 'object': 'chat.completion', 'created': 0, 'model': 'test-model',
            'choices': [{'index': 0, 'message': message, 'finish_reason': 'stop'}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        service = AgentService(db, config=config(), client=create_client(config(), http_client=http))
        result = service.ask('hello')
    assert len(requests) == 2
    assert result['tool_calls'][0]['error']
