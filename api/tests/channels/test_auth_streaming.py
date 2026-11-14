"""Exercise real Starlette Request streams, without buffering via TestClient."""
import asyncio
import threading
import pytest
from fastapi import HTTPException
from starlette.requests import Request, ClientDisconnect
from channels import auth
from channels.models import MAX_BODY_BYTES
from tests.channels.test_inbox import sign, stack, PATH

def request(body=b'{}', *, headers=None, chunks=None, receive=None):
    values = list((sign(body) if headers is None else headers).items())
    scope = {'type': 'http', 'method': 'POST', 'path': PATH, 'query_string': b'', 'headers': [(k.lower().encode(), v.encode()) for k, v in values]}
    calls = []
    pieces = iter([body] if chunks is None else chunks)
    async def reader():
        calls.append(True)
        if receive:
            return await receive()
        chunk = next(pieces, None)
        return {'type': 'http.request', 'body': chunk or b'', 'more_body': chunk is not None}
    return Request(scope, reader), calls

@pytest.mark.parametrize('change,status', [({'X-Channels-Timestamp': '0'}, 401), ({'X-Channels-Timestamp': 'not-time'}, 401), ({'X-Channels-Nonce': 'bad'}, 401), ({'X-Channels-Signature': 'bad'}, 401), ({'content-length': '-1'}, 400), ({'content-length': '1.5'}, 400), ({'content-length': ' 2'}, 400), ({'content-length': '2,2'}, 400), ({'content-length': str(MAX_BODY_BYTES+1)}, 413)])
def test_bad_headers_do_not_read_body(stack, change, status):
    _, db = stack
    req, calls = request(headers=sign(b'{}') | change)
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.authenticated_body(req))
    assert error.value.status_code == status
    assert calls == []
    assert db.channelMessages.count_documents({}) == 0

def test_valid_exact_chunked_bytes_and_nonce(stack):
    _, db = stack
    body = b'{ "text": "caf\xc3\xa9" }'
    req, _ = request(body, chunks=[body[:13], body[13:14], body[14:]])
    assert asyncio.run(auth.authenticated_body(req)) == body
    assert db.channelMessages.count_documents({'kind': 'auth'}) == 1

@pytest.mark.parametrize('length', ['1', '3'])
def test_length_mismatch_does_not_consume_nonce(stack, length):
    _, db = stack
    req, _ = request(headers=sign(b'{}') | {'content-length': length})
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.authenticated_body(req))
    assert error.value.status_code == 400
    assert db.channelMessages.count_documents({}) == 0

def test_capacity_rejects_without_read_and_releases_after_completion(stack, monkeypatch):
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(auth, '_body_slots', gate)
    async def exercise():
        entered, release = asyncio.Event(), asyncio.Event()
        async def reader():
            entered.set()
            await release.wait()
            return {'type': 'http.request', 'body': b'{}', 'more_body': False}
        first, _ = request(receive=reader)
        task = asyncio.create_task(auth.authenticated_body(first))
        await entered.wait()
        second, calls = request()
        with pytest.raises(HTTPException) as error:
            await auth.authenticated_body(second)
        assert error.value.status_code == 503
        assert not calls
        release.set()
        assert await task == b'{}'
        third, _ = request()
        assert await auth.authenticated_body(third) == b'{}'
    asyncio.run(exercise())
    assert gate.acquire(blocking=False)
    gate.release()

@pytest.mark.parametrize('failure', ['timeout', 'disconnect', 'cancel'])
def test_failed_stream_releases_capacity(stack, monkeypatch, failure):
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(auth, '_body_slots', gate)
    monkeypatch.setattr(auth, 'BODY_READ_SECONDS', 0.01)
    async def exercise():
        entered = asyncio.Event()
        async def reader():
            entered.set()
            if failure == 'disconnect':
                return {'type': 'http.disconnect'}
            await asyncio.Event().wait()
        req, _ = request(receive=reader)
        task = asyncio.create_task(auth.authenticated_body(req))
        await entered.wait()
        if failure == 'cancel':
            task.cancel()
        expected = {'timeout': HTTPException, 'disconnect': ClientDisconnect, 'cancel': asyncio.CancelledError}[failure]
        with pytest.raises(expected) as error:
            await task
        if failure == 'timeout':
            assert error.value.status_code == 408
    asyncio.run(exercise())
    assert gate.acquire(blocking=False)
    gate.release()

@pytest.mark.parametrize('header', ['content-length', 'x-channels-timestamp', 'x-channels-nonce', 'x-channels-signature'])
def test_duplicate_headers_rejected_before_stream(stack, header):
    req, calls = request(headers=sign(b'{}') | {'content-length': '2'})
    value = next(value for key, value in req.scope['headers'] if key == header.encode())
    req.scope['headers'].append((header.encode(), value))
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.authenticated_body(req))
    assert error.value.status_code == (400 if header == 'content-length' else 401)
    assert not calls


def test_bad_signature_never_consumes_nonce_and_releases(stack, monkeypatch):
    _, db = stack
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(auth, '_body_slots', gate)
    req, _ = request(headers=sign(b'different body'))
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.authenticated_body(req))
    assert error.value.status_code == 401
    assert db.channelMessages.count_documents({}) == 0
    assert gate.acquire(blocking=False)
    gate.release()


def test_undeclared_oversize_stream_releases_capacity(stack, monkeypatch):
    gate = threading.BoundedSemaphore(1)
    monkeypatch.setattr(auth, '_body_slots', gate)
    body = b'x' * (MAX_BODY_BYTES + 1)
    req, _ = request(body, chunks=[body[:100], body[100:]])
    with pytest.raises(HTTPException) as error:
        asyncio.run(auth.authenticated_body(req))
    assert error.value.status_code == 413
    assert gate.acquire(blocking=False)
    gate.release()
