"""Requirement-derived accounting cases. Expected amounts calculated by hand."""
from copy import deepcopy
import pytest
from jevgauge.analytics import summarize


def event(kind, key, conv='a', time='2026-09-25T12:00:00Z', **fields):
    return dict(schema_version=1, event_id=key, conversation_id=conv,
                timestamp=time, kind=kind, **fields)


def route(conv='a', **fields):
    defaults = dict(default_model='gpt-4.1', selected_model='gpt-4.1-mini',
        outcome='routed', eligibility_source='configured_tiers_and_catalog',
        eligible_models=[{'model':'gpt-4.1-nano', 'capability':0},
                         {'model':'gpt-4.1-mini', 'capability':1},
                         {'model':'gpt-4.1', 'capability':2}])
    defaults.update(fields)
    return event('route', conv+'-route', conv, **defaults)


def attempt(key='one', conv='a', **fields):
    defaults = dict(attempt_id=key, requested_model='gpt-4.1-mini', status='completed',
        usage=dict(input_tokens=1000000, cached_input_tokens=250000,
                   output_tokens=100000, reasoning_tokens=40000, source='provider'))
    defaults.update(fields)
    return event('attempt', key+'-done', conv, **defaults)


def overhead(conv='a', **fields):
    return event('overhead', conv+'-overhead', conv, overhead_usd=.01,
                 overhead_source='measured', **fields)


def test_cache_reasoning_and_hand_calculated_same_cohort():
    result = summarize([route(), attempt(), overhead()])
    # mini: .75*.40 + .25*.10 + .10*1.60 = .485. Reasoning is included.
    # default: .75*2 + .25*.5 + .10*8 = 2.425.
    s = result['summary']
    assert s['routed_cost_usd'] == pytest.approx(.485)
    assert s['default_cost_usd'] == pytest.approx(2.425)
    assert s['median_cost_usd'] == pytest.approx(.485)
    assert s['strongest_cost_usd'] == pytest.approx(2.425)
    assert s['provider_difference_usd'] == pytest.approx(1.94)
    assert s['net_savings_usd'] == pytest.approx(1.93)


def test_retries_rejections_duplicates_and_overhead_once():
    first = attempt(status='rejected')
    events = [route(), event('attempt','one-start',attempt_id='one',status='started'),
              first, deepcopy(first), attempt('retry',fallback=True), overhead(), overhead()]
    s = summarize(events)['summary']
    assert s['request_count'] == 2
    assert s['provider_cost_usd'] == pytest.approx(.970)
    assert s['net_savings_usd'] == pytest.approx(3.87)
    assert s['review_count'] == 1


def test_unknown_missing_manual_never_free_or_router_savings():
    result = summarize([route(), attempt(), attempt('unknown',reported_model='mystery'),
                        attempt('missing',usage=None),attempt('manual',manual_override=True)])
    c = result['coverage']
    assert (c['total_requests'],c['known_usage_requests'],c['priced_requests'],c['comparison_requests']) == (4,3,2,1)
    assert (c['missing_usage_requests'],c['missing_price_requests'],c['manual_requests']) == (1,1,1)
    assert result['summary']['provider_cost_usd'] == pytest.approx(.970)
    assert result['summary']['net_savings_usd'] is None
    assert result['summary']['review_count'] == 1
    assert result['conversations'][0]['manual_override'] is True


def test_historical_default_and_eligibility_negative_difference():
    old = route(default_model='gpt-4.1-nano',eligible_models=[{'model':'gpt-4.1-mini','capability':9}])
    new = route('b',default_model='unknown-default')
    s = summarize([old,attempt(),new,attempt('b-one','b')])['summary']
    assert s['default_cost_usd'] == pytest.approx(.12125)
    assert s['provider_difference_usd'] == pytest.approx(-.36375)
    assert s['median_cost_usd'] == pytest.approx(.485)
    assert s['strongest_cost_usd'] == pytest.approx(.485)
    assert s['comparison_excluded_requests'] == 1


def test_changing_policy_uses_snapshot_before_each_request():
    newer = route(default_model='gpt-4.1-nano',time='2026-09-25T13:00:00Z')
    newer['event_id'] = 'new-policy'
    s = summarize([route(),attempt(),newer,attempt('two',time='2026-09-25T14:00:00Z')])['summary']
    assert s['default_cost_usd'] == pytest.approx(2.54625)


def test_all_baselines_share_cohort_unknown_eligible_excludes_all():
    r = route(eligible_models=[{'model':'gpt-4.1','capability':2},{'model':'unknown','capability':1}])
    result = summarize([r,attempt()])
    assert result['summary']['default_cost_usd'] is None
    assert result['summary']['routed_cost_usd'] is None
    assert result['summary']['provider_cost_usd'] == pytest.approx(.485)
    assert result['coverage']['comparison_requests'] == 0


def test_review_union_disabled_and_confidence_only_excluded():
    events = [route(),attempt(status='failed',usage=None),attempt('two',usage=None),
              route('b',outcome='abstained',reason_code='low_confidence'),
              route('c',outcome='disabled',reason_code='disabled')]
    result = summarize(events)
    assert result['summary']['review_count'] == 1
    assert len(result['conversations'][0]['review_reasons']) >= 2


@pytest.mark.parametrize('date,start,end',[
 ('2026-03-08','2026-03-08T08:00:00Z','2026-03-09T07:00:00Z'),
 ('2026-11-01','2026-11-01T07:00:00Z','2026-11-02T08:00:00Z')])
def test_dst_inclusive_local_date(date,start,end):
    from datetime import datetime,timedelta
    last=(datetime.fromisoformat(end.replace('Z','+00:00'))-timedelta(microseconds=1)).isoformat()
    events=[route(time='2026-01-01T00:00:00Z'),attempt('first',time=start),attempt('last',time=last),attempt('next',time=end)]
    result=summarize(events,start=date,end=date,timezone='America/Los_Angeles')
    assert result['summary']['request_count']==2
    assert result['summary']['default_cost_usd']==pytest.approx(4.85)
    assert result['series'][0]['date']==date


@pytest.mark.parametrize('args',[{'start':'2026-02-30'}, {'start':'2026-10-01','end':'2026-09-01'},
    {'timezone':'bad/zone'},{'start':'2000-01-01','end':'2026-01-01'}])
def test_invalid_filters_rejected(args):
    with pytest.raises(ValueError): summarize([],**args)


def test_empty_missing_usage_and_unknown_cache_are_unavailable():
    assert summarize([])['summary']['provider_cost_usd'] is None
    a=attempt();a['usage']['cached_input_tokens']=None
    result=summarize([route(),a])
    assert result['summary']['provider_cost_usd'] is None
    assert result['coverage']['missing_price_requests']==0
    assert result['coverage']['missing_usage_breakdown_requests']==1
    assert summarize([route()])['conversations'][0]['review_reasons']==['missing_attempt_evidence']


def test_actual_bill_separate_from_estimate_and_project_explicit():
    result=summarize([route(project='Evidence'),attempt(billed_usd=.7)],project='Evidence')
    assert result['summary']['billed_cost_usd']==.7
    assert result['summary']['provider_cost_usd']==pytest.approx(.485)
    assert summarize([route(),attempt()],project='Evidence')['summary']['request_count']==0


def test_attempt_first_timestamp_and_terminal_evidence_not_erased():
    start=event('attempt','start',time='2026-09-25T23:59:00Z',attempt_id='one',status='started')
    done=attempt(time='2026-09-26T00:01:00Z')
    result=summarize([route(),done,start],start='2026-09-25',end='2026-09-25')
    assert result['summary']['request_count']==1
    assert result['summary']['provider_cost_usd']==pytest.approx(.485)


def test_usage_payload_and_eligibility_never_leak_arbitrary_fields():
    a=attempt(); a['usage']['prompt']='private text'
    r=route();r['eligible_models'][0]['secret']='private value'
    import json
    response=json.dumps(summarize([r,a]))
    assert 'private' not in response


def test_route_technical_abstention_review_and_unknown_attempt_not_zero():
    result=summarize([route(outcome='unrouted/default',reason_code='selection_timeout')])
    assert result['summary']['review_count']==1
    assert 'routing_failure' in result['conversations'][0]['review_reasons']


def test_actual_model_wins_and_capability_ties_not_price_order():
    r=route(eligible_models=[{'model':'gpt-4.1-nano','capability':9},
                             {'model':'gpt-4.1','capability':1}])
    result=summarize([r,attempt(reported_model='gpt-4.1-nano')])
    assert result['summary']['routed_cost_usd']==pytest.approx(.12125)
    assert result['summary']['strongest_cost_usd']==pytest.approx(.12125)
    assert result['summary']['median_cost_usd']==pytest.approx(1.273125)


def test_bounded_generator_does_not_materialize_unbounded_history():
    import jevgauge.analytics as analytics
    old=analytics.MAX_EVENTS
    try:
        analytics.MAX_EVENTS=2
        with pytest.raises(ValueError,match='bound'): summarize([route(),attempt(),overhead()])
    finally: analytics.MAX_EVENTS=old


def test_demo_deterministic_isolated_and_valid_period():
    from jevgauge import demo
    first=demo.events(); second=demo.events()
    assert first==second
    first[0]['project']='modified'
    assert second[0]['project']!='modified'
    data=summarize(second,start='2026-09-01',end='2026-09-30')
    assert data['summary']['conversation_count']==28
    assert data['coverage']['total_requests']==65
    assert data['summary']['review_count']==3
    assert data['summary']['net_savings_usd'] is None
    assert data['coverage']['manual_requests']==2


def test_override_event_applies_only_to_later_attempts():
    override=event('override','changed',time='2026-09-25T13:00:00Z',manual_override=True)
    result=summarize([route(),attempt(),override,attempt('after',time='2026-09-25T14:00:00Z')])
    assert result['coverage']['manual_requests']==1
    assert result['coverage']['comparison_requests']==1


def test_filtered_row_uses_historical_metadata_not_future_policy():
    old=route(time='2026-09-01T00:00:00Z')
    new=route(time='2026-09-20T00:00:00Z',selected_model='future-model');new['event_id']='new'
    result=summarize([old,new,attempt(time='2026-09-01T12:00:00Z')],start='2026-09-01',end='2026-09-01')
    assert result['conversations'][0]['selected_model']=='gpt-4.1-mini'


def test_missing_pricing_file_keeps_evidence_visible(monkeypatch):
    import jevgauge.analytics as analytics
    class Absent:
        def joinpath(self,name): return self
        def read_text(self): raise FileNotFoundError()
    monkeypatch.setattr(analytics,'files',lambda _:Absent())
    result=summarize([route(),attempt()])
    assert result['summary']['request_count']==1
    assert result['summary']['provider_cost_usd'] is None
    assert result['pricing']['version']=='unavailable'


def test_highest_capability_tie_selects_actual_model_deterministically():
    r=route(eligible_models=[{'model':'gpt-4.1-nano','capability':2},
                             {'model':'gpt-4.1-mini','capability':2}])
    s=summarize([r,attempt()])['summary']
    # Tied rank chooses lexicographically first model, mini, never average-model cost.
    assert s['strongest_cost_usd']==pytest.approx(.485)


@pytest.mark.parametrize('count',[10000,50000])
def test_representative_large_history_remains_bounded_and_accounted(count):
    from time import perf_counter
    records=[event('attempt',str(i),attempt_id=str(i),requested_model='gpt-4.1-mini',
        status='completed',usage=dict(input_tokens=1000,cached_input_tokens=0,
                                    output_tokens=100,reasoning_tokens=0,source='provider')) for i in range(count)]
    started=perf_counter()
    result=summarize(records)
    elapsed=perf_counter()-started
    assert result['summary']['request_count']==count
    assert result['summary']['provider_cost_usd']==pytest.approx(count*.00056)
    assert result['summary']['review_count']==1
    assert result['summary']['default_cost_usd'] is None
    # Coarse ceiling catches accidental quadratic scans without microbenchmark fragility.
    assert elapsed < 20


def test_route_outcomes_do_not_double_count_provider_failure():
    result=summarize([route(),attempt(status='rejected'),route('disabled',outcome='disabled'),
        route('confidence',outcome='abstained',reason_code='low_confidence'),
        route('timeout',outcome='abstained',reason_code='routing deadline exceeded')])
    s=result['summary']
    assert (s['routed_count'],s['abstained_count'],s['disabled_count'],s['routing_failed_count'])==(1,1,1,1)
    assert s['failed_count']==2  # technical failure conversations, not a disjoint outcome
