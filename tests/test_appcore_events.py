"""Tests for appcore/events.py"""
from appcore.events import Event, EventBus, EVT_STEP_UPDATE


def test_subscribe_and_publish_calls_handler():
    bus = EventBus()
    received = []
    bus.subscribe(lambda e: received.append(e))
    event = Event(type=EVT_STEP_UPDATE, task_id="t1", payload={"step": "asr", "status": "done"})
    bus.publish(event)
    assert len(received) == 1
    assert received[0] is event


def test_multiple_handlers_all_receive_event():
    bus = EventBus()
    calls = []
    bus.subscribe(lambda e: calls.append("h1"))
    bus.subscribe(lambda e: calls.append("h2"))
    bus.publish(Event(type=EVT_STEP_UPDATE, task_id="t1"))
    assert calls == ["h1", "h2"]


def test_publish_with_no_subscribers_does_not_raise():
    bus = EventBus()
    bus.publish(Event(type=EVT_STEP_UPDATE, task_id="t1"))  # must not raise


def test_event_payload_defaults_to_empty_dict():
    event = Event(type=EVT_STEP_UPDATE, task_id="t1")
    assert event.payload == {}
