import pytest
from channels.consent import is_stop_command
from channels import photos
from db import conversations
from tests.channels.test_inbox import stack, add_contact, envelope, send
from tests.channels.test_photos import envelope as photo_envelope

@pytest.mark.parametrize('text', ['Stop.', 'STOP!', 'stop please', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT', 'ＳＴＯＰ', 's\u200bto\u200dp', '🛑STOP!🙏', 'stop thank you', '"STOP"'])
def test_command_normalization(text):
    assert is_stop_command(text)

@pytest.mark.parametrize('text', ['I told him to stop', 'the bus stop', 'stopwatch', 'do not stop', 'stop was what he said', 'the end', 'cancel our picnic', ''])
def test_story_is_not_command(text):
    assert not is_stop_command(text)

def test_stop_revokes_all_families_even_when_receipt_fails(stack, monkeypatch):
    client, db = stack
    add_contact(db, family='family-b', person='person-b')
    def fail(*args, **kwargs):
        assert db.contacts.count_documents({'status': 'active'}) == 0
        raise RuntimeError('receipt failure')
    monkeypatch.setattr(conversations, 'save_inbound', fail)
    result = send(client, envelope(text='STOP!'))
    assert result.status_code == 202
    assert result.json()['stopped'] is True
    assert db.contacts.count_documents({'status': 'active'}) == 0

def test_photo_stop_precedes_decode_and_storage(stack, monkeypatch):
    client, db = stack
    monkeypatch.setattr(photos, 'decode_photo', lambda *_: pytest.fail('decoded STOP photo'))
    monkeypatch.setattr(conversations, 'save_photo_inbound', lambda *_: pytest.fail('stored STOP photo'))
    result = send(client, photo_envelope(data=b'invalid image', caption='Stop please!'), path='/internal/channels/inbound-photo')
    assert result.status_code == 202
    assert result.json()['stopped'] is True
    assert db.contacts.count_documents({'status': 'active'}) == 0
    receipt = db.channelMessages.find_one({'kind': 'inbound'})
    assert receipt['status'] == 'stop_requested'
    assert 'dataBase64' not in receipt
    assert db.memories.count_documents({}) == 0

def test_unknown_photo_stop_has_normal_unknown_contact_response(stack, monkeypatch):
    client, _ = stack
    monkeypatch.setattr(photos, 'decode_photo', lambda *_: pytest.fail('decoded unknown STOP photo'))
    result = send(client, photo_envelope(senderId='unknown', caption='QUIT'), path='/internal/channels/inbound-photo')
    assert result.status_code == 404
