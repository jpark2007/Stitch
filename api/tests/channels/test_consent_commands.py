import pytest
from channels.consent import is_stop_command
from channels import photos
from db import conversations
from tests.channels.test_inbox import stack, add_contact, envelope, send
from tests.channels.test_photos import envelope as photo_envelope

@pytest.mark.parametrize('text', ['Stop.', 'STOP!', 'stop please', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT', 'ＳＴＯＰ', 's\u200bto\u200dp', '🛑STOP!🙏', 'stop thank you', '"STOP"'])
def test_command_normalization(text):
    assert is_stop_command(text)

@pytest.mark.parametrize('text', ['I told him to stop', 'the bus stop', 'stopwatch', 'do not stop', 'stop was what he said', 'the end', 'cancel our picnic', 'sto\u0301p', 'I told him to stop ❤️', ''])
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

@pytest.mark.parametrize('caption', ['Stop please!', 'STOP ❤️', 'STOP‼️', 'STOP\U000e0100'])
def test_photo_stop_precedes_decode_and_storage(stack, monkeypatch, caption):
    client, db = stack
    monkeypatch.setattr(photos, 'decode_photo', lambda *_: pytest.fail('decoded STOP photo'))
    monkeypatch.setattr(conversations, 'save_photo_inbound', lambda *_: pytest.fail('stored STOP photo'))
    result = send(client, photo_envelope(data=b'invalid image', caption=caption), path='/internal/channels/inbound-photo')
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


@pytest.mark.parametrize('text', ['STOP‼️', 'STOP ❤️', 'STOP\ufe0e', 'STOP\ufe00', 'STOP\U000e0100', 'STOP\U000e01ef'])
def test_variation_selector_text_revokes_consent(stack, text):
    client, db = stack
    response = send(client, envelope(text=text))
    assert response.status_code == 202
    assert response.json()['stopped'] is True
    assert db.contacts.count_documents({'status': 'active'}) == 0


def pending_question(db, *, family='family-a', handle='alice', kind='answer'):
    contact_id = f'{family}:{handle}'
    db.conversations.insert_one({
        '_id': conversations.conversation_key(family, contact_id),
        'familyId': family, 'contactId': contact_id,
        'pending': {'kind': kind, 'question': 'What happened next?'},
    })


@pytest.mark.parametrize('kind', ['answer', 'props', 'merge'])
@pytest.mark.parametrize('text', ['Cancel.', 'End thanks', 'Quit!', 'ＣＡＮＣＥＬ please'])
def test_ambiguous_command_is_reply_while_studio_question_pending(stack, text, kind):
    client, db = stack
    pending_question(db, kind=kind)
    response = send(client, envelope(text=text))
    assert response.status_code == 202
    assert not response.json().get('stopped')
    assert db.contacts.find_one({})['status'] == 'active'
    assert db.channelMessages.find_one({'kind': 'inbound'})['status'] == 'received'


@pytest.mark.parametrize('text', ['STOP', 'STOPALL', 'UNSUBSCRIBE'])
def test_unconditional_opt_out_does_not_depend_on_question_lookup(stack, monkeypatch, text):
    client, db = stack
    pending_question(db)
    monkeypatch.setattr(conversations, 'has_pending_question', lambda *_: pytest.fail('unnecessary question lookup'))
    assert send(client, envelope(text=text)).json()['stopped']
    assert db.contacts.find_one({})['status'] == 'stopped'


def test_ambiguous_command_with_pending_question_in_other_family_does_not_stop_any_family(stack):
    client, db = stack
    add_contact(db, family='family-b', person='person-b')
    pending_question(db, family='family-b')
    response = send(client, envelope(text='CANCEL'))
    assert response.status_code == 409  # Explicit family selection, never an inferred choice.
    assert db.contacts.count_documents({'status': 'active'}) == 2


def test_pending_question_lookup_is_scoped_and_does_not_create_conversation(stack):
    _, db = stack
    pending_question(db)
    assert conversations.has_pending_question(db, 'family-a', 'family-a:alice')
    assert not conversations.has_pending_question(db, 'family-b', 'family-a:alice')
    assert not conversations.has_pending_question(db, 'family-a', 'another-contact')
    assert db.conversations.count_documents({}) == 1


def test_photo_cancel_is_caption_while_question_pending(stack, monkeypatch):
    client, db = stack
    pending_question(db)
    from tests.channels.test_photos import image_bytes
    monkeypatch.setattr(photos, 'decode_photo', lambda *_: image_bytes())
    captured = []
    def save(db, contact, message, data):
        captured.append(message['caption'])
        return False
    monkeypatch.setattr(conversations, 'save_photo_inbound', save)
    response = send(client, photo_envelope(caption='Cancel please'), path='/internal/channels/inbound-photo')
    assert response.status_code == 202
    assert not response.json().get('stopped')
    assert captured == ['Cancel please']
    assert db.contacts.find_one({})['status'] == 'active'


@pytest.mark.parametrize('suffix', ['\u3164', '\u115f', '\u1160', '\uffa0', '\u034f'])
def test_invisible_fillers_do_not_hide_stop(stack, suffix):
    client, db = stack
    assert send(client, envelope(text='STOP' + suffix)).json()['stopped']
    assert db.contacts.find_one({})['status'] == 'stopped'
