"""Deterministic synthetic evidence, never persisted or mixed with live events."""
from datetime import datetime, timedelta, timezone

AS_OF = '2026-09-25T18:02:00Z'


def events():
    """Return fresh demo-only events. IDs and values have no production provenance."""
    result = []
    models = ['gpt-4.1-nano','gpt-4.1-mini','gpt-4.1']
    for i in range(28):
        conv=f'demo-{i+1:03}'
        stamp=datetime(2026,9,1,18,tzinfo=timezone.utc)+timedelta(days=min(i,24),seconds=i)
        base=dict(schema_version=1,conversation_id=conv,project=['Atlas','Signal','Notebook'][i%3],
                  timestamp=stamp.isoformat().replace('+00:00','Z'))
        chosen=models[i%3]
        manual=i==9
        disabled=i==12
        confidence=i==15
        result.append(dict(base,event_id=f'{conv}-route',kind='route',provider='demo',
            default_model='gpt-4.1',default_effort=None,selected_model=chosen,
            requested_effort=None,model_tier=['economical','balanced','strongest'][i%3],
            effort_tier=None,reason_code='disabled' if disabled else 'low_confidence' if confidence else 'demo_synthetic_route',
            outcome='disabled' if disabled else 'abstained' if confidence else 'routed',
            policy_version='demo-v1',latency_ms=80+i*9,manual_override=manual,
            eligible_models=[dict(model=m,capability=j) for j,m in enumerate(models)],
            eligibility_source='demo_configured_tiers'))
        if disabled or confidence:
            continue
        result.append(dict(base,event_id=f'{conv}-overhead',kind='overhead',
                           overhead_usd=.004,overhead_source='demo_assumption'))
        for j in range(1+i%4):
            attempt_id=f'{conv}-request-{j}'
            when=(stamp+timedelta(seconds=j+1)).isoformat().replace('+00:00','Z')
            usage=dict(input_tokens=10000*(i+1),cached_input_tokens=2000*(i+1),
                       output_tokens=1200*(j+1),reasoning_tokens=0,source='demo')
            result.append(dict(base,event_id=attempt_id+'-done',timestamp=when,kind='attempt',
                attempt_id=attempt_id,requested_model=chosen,reported_model='unknown-demo-model' if i==20 else chosen,
                requested_effort=None,reported_effort=None,provider='demo',
                status='rejected' if i==5 and j==0 else 'completed',fallback=i==5 and j>0,
                manual_override=manual,usage=None if i==22 else usage))
    return result
