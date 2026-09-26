"""Independent reviewer cases, hand calculated without author fixture helpers."""
import json
import pytest
from jevgauge.analytics import summarize


def evidence(kind, identity, when='2026-09-10T12:00:00Z', conversation='review', **fields):
    return {'schema_version':1,'event_id':identity,'kind':kind,'conversation_id':conversation,'timestamp':when,**fields}


def baseline(**changes):
    row = evidence('route','decision',when='2026-09-01T00:00:00Z',outcome='routed',
        default_model='gpt-4.1',selected_model='gpt-4.1-mini',eligibility_source='captured configured ranks',
        eligible_models=[{'model':'gpt-4.1-nano','capability':9}, {'model':'gpt-4.1','capability':0}])
    row.update(changes)
    return row


def request(identity, **changes):
    row = evidence('attempt',identity+'-done',attempt_id=identity,status='completed',
        requested_model='gpt-4.1-mini',usage={'source':'provider','input_tokens':800000,
        'cached_input_tokens':300000,'output_tokens':200000,'reasoning_tokens':170000})
    row.update(changes)
    return row


def test_review_literal_cache_reasoning_retry_manual_billing_and_union():
    r=request('reject',status='rejected',billed_usd=.9)
    start=evidence('attempt','reject-start',when='2026-09-10T11:59:59Z',attempt_id='reject',status='started')
    paid=request('retry',fallback=True)
    manual=request('manual',manual_override=True)
    unknown=request('unknown',reported_model='unlisted')
    absent=request('absent',usage=None,billed_usd=.2)
    result=summarize([baseline(),start,r,r,paid,manual,unknown,absent])
    s=result['summary']; c=result['coverage']
    # mini = .5*.4 + .3*.1 + .2*1.6 = .55, reasoning already inside .2.
    # default = .5*2 + .3*.5 + .2*8 = 2.75.
    # capacity winner nano = .5*.1 + .3*.025 + .2*.4 = .1375.
    assert s['request_count']==5
    assert s['provider_cost_usd']==pytest.approx(1.65)  # includes the manual .55
    assert s['billed_cost_usd']==pytest.approx(1.1)  # not substituted for API estimates
    assert s['routed_cost_usd']==pytest.approx(1.10)
    assert s['default_cost_usd']==pytest.approx(5.50)
    assert s['strongest_cost_usd']==pytest.approx(.275)  # capability rank, not price
    assert s['median_cost_usd']==pytest.approx(2.8875)
    assert s['provider_difference_usd']==pytest.approx(4.4)
    assert s['review_count']==1
    assert c['comparison_requests']==2 and c['manual_requests']==1
    assert c['missing_usage_requests']==1 and c['missing_price_requests']==1
    assert s['net_savings_usd'] is None


def test_review_overhead_dedup_and_negative_net_with_same_cohort():
    charge=evidence('overhead','one-charge',overhead_usd=3,overhead_source='provider invoice')
    s=summarize([baseline(),request('one'),charge,charge])['summary']
    assert s['overhead_usd']==3
    assert s['net_savings_usd']==pytest.approx(-.8)
    s=summarize([baseline(),request('one'),charge,request('manual',manual_override=True)])['summary']
    assert s['overhead_available'] is True
    assert s['net_savings_usd'] is None  # Never subtract full overhead from a partial cohort.


def test_review_start_date_owns_request_and_historical_default_survives():
    prior=baseline()
    newer=baseline(event_id='changed',timestamp='2026-11-02T12:00:00Z',default_model='gpt-4.1-nano')
    start=evidence('attempt','a-start',when='2026-11-01T04:00:00Z',attempt_id='a',status='started')
    terminal=request('a',timestamp='2026-11-02T06:00:00Z')
    outside=request('next',timestamp='2026-11-02T05:00:00Z')
    result=summarize([newer,terminal,start,prior,outside],start='2026-11-01',end='2026-11-01',timezone='America/New_York')
    assert result['summary']['request_count']==1  # 25-hour local day, excludes midnight next day.
    assert result['summary']['default_cost_usd']==pytest.approx(2.75)
    assert result['conversations'][0]['default_model']=='gpt-4.1'


def test_review_missing_cache_is_usage_breakdown_gap_not_missing_price():
    r=request('unknown-cache')
    r['usage']['cached_input_tokens']=None
    result=summarize([baseline(),r])
    assert result['summary']['provider_cost_usd'] is None
    assert result['coverage']['missing_price_requests']==0
    assert result['coverage']['missing_usage_breakdown_requests']==1
    assert 'missing_usage_breakdown' in result['conversations'][0]['review_reasons']


@pytest.mark.parametrize('rate',[{'input':-1,'cached_input':0,'output':1}, {'input':1},
    {'input':1,'cached_input':float('nan'),'output':2},
    {'input':10**1000,'cached_input':0,'output':1}])
def test_review_corrupt_pricing_is_unavailable_not_negative_or_crash(monkeypatch,rate):
    import jevgauge.analytics as module
    class Corrupt:
        def joinpath(self, _): return self
        def read_text(self): return json.dumps({'version':'broken','currency':'USD','rates':{'gpt-4.1-mini':rate}})
    monkeypatch.setattr(module,'files',lambda _:Corrupt())
    result=module.summarize([baseline(),request('one')])
    assert result['summary']['provider_cost_usd'] is None
    assert result['coverage']['missing_price_requests']==1


def test_review_model_chart_partitions_conversations_and_requested_attempts():
    before=request('before',requested_model='requested-first',reported_model='provider-alias')
    after=request('after',requested_model='requested-final',reported_model='another-alias',timestamp='2026-09-10T13:00:00Z')
    no_attempt=baseline(event_id='no-attempt',conversation_id='route-only',outcome='disabled')
    result=summarize([baseline(),before,after,no_attempt])
    rows={r['model']:r for r in result['models']}
    assert sum(r['conversations'] for r in rows.values()) == 2
    assert sum(r['requests'] for r in rows.values()) == 2
    assert rows['requested-first']['requests']==1 and rows['requested-first']['conversations']==0
    assert rows['requested-final']['requests']==1 and rows['requested-final']['conversations']==1
    assert rows['Unavailable']['conversations']==1 and rows['Unavailable']['requests']==0
    assert 'provider-alias' not in rows and 'another-alias' not in rows


@pytest.mark.parametrize('outcome,reason',[('disabled','routing disabled'),('abstained','low Jev confidence')])
def test_review_disabled_routing_with_healthy_request_does_not_need_baseline_review(outcome,reason):
    disabled=baseline(outcome=outcome,reason_code=reason,eligible_models=[],eligibility_source=None)
    result=summarize([disabled,request('default',requested_model='gpt-4.1')])
    assert result['summary']['review_count']==0
    assert result['coverage']['comparison_requests']==0
    failed=summarize([disabled,request('default',requested_model='gpt-4.1',status='rejected')])
    assert failed['summary']['review_count']==1
