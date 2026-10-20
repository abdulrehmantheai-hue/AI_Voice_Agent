import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

SAMPLE = dict(first_name='Jane', last_name='Doe', date_of_birth='1990-05-12', sex='Female', phone_number='2125550199', address_line_1='123 Main Street', city='New York', state='NY', zip_code='10001')

@pytest.fixture(scope='module')
def client():
    tmp = tempfile.TemporaryDirectory()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv('DATABASE_URL', 'sqlite:///' + str(Path(tmp.name) / 'tests.db'))
    monkeypatch.setenv('API_KEY', 'test_api_secret')
    monkeypatch.setenv('DASHBOARD_PASSWORD', 'test_dashboard_secret')
    monkeypatch.setenv('WEBHOOK_SECRET', 'test_webhook_secret')
    engine = None
    try:
        from app import app, engine

        with TestClient(app, raise_server_exceptions=False) as test_client:
            yield test_client
    finally:
        if engine is not None:
            engine.dispose()
        monkeypatch.undo()
        tmp.cleanup()

HEADERS = {'X-API-Key': 'test_api_secret'}

def register_patient(client):
    response = client.post('/patients', json=SAMPLE, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()['data']['patient_id']

def test_auth(client):
    assert client.get('/patients').status_code == 401
    assert client.get('/health').status_code == 200

def test_crud_and_persistence(client):
    identifier = register_patient(client)
    assert client.get('/patients?last_name=Doe', headers=HEADERS).json()['data'][0]['patient_id'] == identifier
    assert client.get(f'/patients/{identifier}', headers=HEADERS).status_code == 200
    updated = client.put(f'/patients/{identifier}', json={'city': 'Albany'}, headers=HEADERS)
    assert updated.status_code == 200
    assert updated.json()['data']['city'] == 'Albany'
    assert client.delete(f'/patients/{identifier}', headers=HEADERS).status_code == 200
    missing = client.get(f'/patients/{identifier}', headers=HEADERS)
    assert missing.status_code == 404
    assert missing.json()['error']['code'] == 'NOT_FOUND'

def test_invalid(client):
    for field, value in [('date_of_birth', '2999-01-01'), ('phone_number', '123'), ('zip_code', 'nope'), ('state','ZZ'), ('first_name', 'Jane123')]:
        payload = dict(SAMPLE, **{field: value})
        response = client.post('/patients', json=payload, headers=HEADERS)
        assert response.status_code == 422
        assert response.json()['data'] is None
        assert response.json()['error']['code'] == 'VALIDATION_ERROR'
        assert field in {item['field'] for item in response.json()['error']['fields']}

def test_invalid_partial_updates_return_field_errors(client):
    identifier = register_patient(client)
    for payload, field, message in (
        ({'date_of_birth': '2035-01-01'}, 'date_of_birth', 'Date of birth must not be in the future'),
        ({'phone_number': '123'}, 'phone_number', 'Provide a valid US ten-digit phone number'),
    ):
        response = client.put(f'/patients/{identifier}', json=payload, headers=HEADERS)
        assert response.status_code == 422
        assert response.json() == {
            'data': None,
            'error': {
                'code': 'VALIDATION_ERROR',
                'message': 'Patient validation failed',
                'fields': [{'field': field, 'message': message}],
            },
        }

def test_multiple_update_errors_and_data_integrity(client):
    identifier = register_patient(client)
    original = client.get(f'/patients/{identifier}', headers=HEADERS).json()['data']
    response = client.put(
        f'/patients/{identifier}',
        json={'date_of_birth': '2035-01-01', 'phone_number': '123'},
        headers=HEADERS,
    )
    assert response.status_code == 422
    errors = response.json()['error']['fields']
    assert {item['field']: item['message'] for item in errors} == {
        'date_of_birth': 'Date of birth must not be in the future',
        'phone_number': 'Provide a valid US ten-digit phone number',
    }
    assert client.get(f'/patients/{identifier}', headers=HEADERS).json()['data'] == original

def test_valid_partial_update(client):
    identifier = register_patient(client)
    response = client.put(
        f'/patients/{identifier}',
        json={'city': 'Brooklyn', 'zip_code': '11201'},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()['data']['city'] == 'Brooklyn'
    assert response.json()['data']['zip_code'] == '11201'
    assert response.json()['error'] is None

def test_nonexistent_patient_has_standard_error_envelope(client):
    invalid_id = client.get('/patients/not-a-uuid', headers=HEADERS)
    assert invalid_id.status_code == 400
    assert invalid_id.json() == {
        'data': None,
        'error': {'code': 'BAD_REQUEST', 'message': 'Invalid UUID'},
    }

    response = client.get('/patients/8b9fc96e-00b8-4d92-9c1d-8ad894ace53c', headers=HEADERS)
    assert response.status_code == 404
    assert response.json() == {
        'data': None,
        'error': {'code': 'NOT_FOUND', 'message': 'Patient not found'},
    }

def test_database_write_failure_rolls_back_and_returns_safe_error(client, monkeypatch):
    identifier = register_patient(client)
    original = client.get(f'/patients/{identifier}', headers=HEADERS).json()['data']

    def fail_commit(_self):
        raise RuntimeError('simulated database failure with sensitive details')

    monkeypatch.setattr(Session, 'commit', fail_commit)
    response = client.put(
        f'/patients/{identifier}',
        json={'city': 'Should Not Persist'},
        headers=HEADERS,
    )
    assert response.status_code == 500
    assert response.json() == {
        'data': None,
        'error': {
            'code': 'INTERNAL_SERVER_ERROR',
            'message': 'Internal server error',
        },
    }
    assert 'simulated database failure' not in response.text
    assert client.get(f'/patients/{identifier}', headers=HEADERS).json()['data'] == original

def test_vapi_confirmation_and_response(client):
    def tool(confirmed):
        return {'message': {'type': 'tool-calls', 'toolCallList': [{'id': 'tc1', 'function': {'name': 'save_patient_registration', 'arguments': dict(SAMPLE, confirmed=confirmed)}}]}}
    h = {'X-Webhook-Secret': 'test_webhook_secret'}
    assert client.post('/vapi/webhook', json=tool(False), headers=h).json()['results'][0].get('error')
    result = client.post('/vapi/webhook', json=tool(True), headers=h)
    assert result.status_code == 200
    assert 'patient_id' in result.json()['results'][0]['result']
    find = {'message': {'type': 'tool-calls', 'toolCallList': [{'id':'tc2', 'function': {'name': 'find_patient_by_phone', 'arguments': {'phone_number':'2125550199'}}}]}}
    assert '"found":true' in client.post('/vapi/webhook', json=find, headers=h).json()['results'][0]['result']

def test_login(client):
    r = client.post('/dashboard/login', json={'password':'test_dashboard_secret'})
    assert r.status_code == 200
    assert client.get('/patients').status_code == 200
    assert client.post('/dashboard/logout').status_code == 200
