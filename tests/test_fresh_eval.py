"""Fresh-context regressions from externally visible evidence contracts."""
import pytest
from jevgauge.analytics import summarize


def _event(kind, event_id, timestamp, **fields):
    return dict(schema_version=1, kind=kind, event_id=event_id,
                conversation_id='cross-midnight', timestamp=timestamp, **fields)


def test_terminal_record_does_not_create_a_second_day_of_request_activity():
    events = [
        _event('route', 'route', '2026-09-25T23:58:00Z', outcome='routed',
               default_model='gpt-4.1', selected_model='gpt-4.1-mini',
               eligible_models=[{'model':'gpt-4.1-mini', 'capability':0}],
               eligibility_source='captured catalog'),
        _event('attempt', 'start', '2026-09-25T23:59:00Z', attempt_id='request',
               status='started', requested_model='gpt-4.1-mini'),
        _event('attempt', 'done', '2026-09-26T00:01:00Z', attempt_id='request',
               status='completed', requested_model='gpt-4.1-mini',
               usage=dict(input_tokens=1000, cached_input_tokens=0,
                          output_tokens=100, reasoning_tokens=0, source='provider')),
    ]
    started_day = summarize(events, start='2026-09-25', end='2026-09-25')
    assert started_day['summary']['request_count'] == 1
    assert started_day['summary']['conversation_count'] == 1
    assert started_day['summary']['provider_cost_usd'] == pytest.approx(0.00056)
    assert started_day['summary']['default_cost_usd'] == pytest.approx(0.0028)
    # The documented start-time attribution already counted this complete request
    # on the preceding day. Its terminal record is not another conversation visit.
    finished_day = summarize(events, start='2026-09-26', end='2026-09-26')
    assert finished_day['summary']['request_count'] == 0
    assert finished_day['summary']['conversation_count'] == 0
    assert finished_day['models'] == []


def test_route_only_activity_remains_visible_without_fabricating_requests():
    result = summarize([
        _event('route', 'disabled', '2026-09-26T00:01:00Z', outcome='disabled',
               reason_code='routing disabled')
    ], start='2026-09-26', end='2026-09-26')
    assert result['summary']['conversation_count'] == 1
    assert result['summary']['request_count'] == 0
    assert result['summary']['review_count'] == 0
