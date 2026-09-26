"""Evidence-only USD accounting. Null means unavailable, never a zero estimate."""
from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone as dt_timezone
from decimal import Decimal
from importlib.resources import files
import json
import math
from statistics import median
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

MAX_EVENTS = 50000
MAX_RANGE_DAYS = 3660
LIMITATIONS = [
    'API-equivalent same-usage comparisons do not prove lower subscription charges, causal savings, or preserved task quality.',
    'Request-count coverage does not bound missing dollars. Missing usage and unknown pricing are not zero cost.',
    'Capability is the configured tier rank captured at routing time, not an objective quality measurement.',
    'Manual attempts are included in observed provider estimates and excluded from router comparisons.',
    'Published rates are a versioned snapshot; actual billed charges are shown separately when reported.',
    'Net savings require complete comparison and overhead evidence for every selected conversation.',
]


def _valid_rate(rate):
    try:
        return (isinstance(rate, dict) and all(
            type(rate.get(key)) in (int, float) and math.isfinite(rate[key]) and rate[key] >= 0
            for key in ('input', 'cached_input', 'output')))
    except OverflowError:
        return False


def _has_price(model, pricing):
    return _valid_rate(pricing.get('rates', {}).get(model))


def _known_cache(usage):
    return (isinstance(usage, dict) and type(usage.get('cached_input_tokens')) is int
            and type(usage.get('input_tokens')) is int
            and 0 <= usage['cached_input_tokens'] <= usage['input_tokens'])


def load_pricing():
    try:
        table = json.loads(files('jevgauge').joinpath('pricing.json').read_text())
        if (not isinstance(table.get('rates'),dict) or table.get('currency') != 'USD'
                or not all(isinstance(model, str) and _valid_rate(rate) for model, rate in table['rates'].items())):
            raise ValueError('Invalid rate table')
        return table
    except (OSError,ValueError,AttributeError):
        return dict(version='unavailable',currency='USD',rates={},sources=[],
                    conditions=['Pricing source unavailable; evidence remains visible and all estimates are unpriced.'])


def _timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timestamp must include a timezone')
    return result.astimezone(dt_timezone.utc)


def _bounds(start, end, timezone):
    try:
        zone = ZoneInfo(timezone)
        first = date.fromisoformat(start) if start else None
        last = date.fromisoformat(end) if end else None
        if first and start != first.isoformat() or last and end != last.isoformat():
            raise ValueError('Dates must be YYYY-MM-DD')
        if first and last and not 0 <= (last-first).days < MAX_RANGE_DAYS:
            raise ValueError('Invalid date range')
        low = datetime.combine(first, time.min, zone) if first else None
        high = datetime.combine(last+timedelta(days=1), time.min, zone) if last else None
        return zone, low, high
    except (ZoneInfoNotFoundError, TypeError, OverflowError) as exc:
        raise ValueError('Invalid date filters') from exc


def _known_usage(usage):
    if not isinstance(usage, dict) or usage.get('source') not in ('provider', 'demo'):
        return False
    return all(type(usage.get(k)) is int and usage[k] >= 0
               for k in ('input_tokens','output_tokens'))


def _estimate(model, usage, pricing):
    if not _known_usage(usage):
        return None
    cached = usage.get('cached_input_tokens')
    if not _known_cache(usage):
        return None
    rate = pricing['rates'].get(model)
    if not _valid_rate(rate):
        return None
    return (Decimal(usage['input_tokens']-cached)*Decimal(str(rate['input']))
            + Decimal(cached)*Decimal(str(rate['cached_input']))
            + Decimal(usage['output_tokens'])*Decimal(str(rate['output'])))/Decimal(1000000)


def _amount(value):
    return None if value is None else float(value)


def _sum(values):
    present = [v for v in values if v is not None]
    return sum(present, Decimal(0)) if present else None


def _charge(value):
    if type(value) in (int,float) and math.isfinite(value) and value >= 0:
        return Decimal(str(value))
    return None


def summarize(events, *, start=None, end=None, timezone='UTC', project=None, pricing=None):
    """Aggregate attempts by their start time, retaining preceding route snapshots.

    A request is one distinct (conversation_id, attempt_id), even when both start
    and terminal records exist. Date endpoints are inclusive local calendar days.
    All input must be normalized telemetry; only allowlisted fields are returned.
    """
    zone, low, high = _bounds(start, end, timezone)
    if project is not None and (not isinstance(project,str) or len(project)>256):
        raise ValueError('Invalid project filter')
    pricing = pricing if pricing is not None else load_pricing()
    unique = {}
    for index, event in enumerate(events):
        if index >= MAX_EVENTS:
            raise ValueError('History exceeds dashboard bound')
        if event.get('schema_version') != 1:
            raise ValueError('Unsupported event version')
        unique.setdefault(event['event_id'], event)
    ordered = sorted(unique.values(), key=lambda e: (_timestamp(e['timestamp']), e['kind'] != 'route', e['event_id']))
    groups = defaultdict(list)
    for event in ordered:
        groups[event['conversation_id']].append(event)
    in_range = lambda stamp: (low is None or stamp>=low) and (high is None or stamp<high)
    rows, all_attempts, day_entries = [], [], defaultdict(list)
    overhead_by_day = defaultdict(list)
    options = set()
    for conv, records in groups.items():
        routes = [r for r in records if r['kind']=='route']
        route_times = [_timestamp(r['timestamp']) for r in routes]
        display_routes = [r for r in routes if high is None or _timestamp(r['timestamp']) < high]
        metadata = display_routes[-1] if display_routes else {}
        overrides = [r for r in records if r['kind']=='override']
        override_times = [_timestamp(r['timestamp']) for r in overrides]
        attribution = next((r.get('project') for r in records if r.get('project')), None)
        options.add(attribution or 'Unattributed')
        if project is not None and project != (attribution or 'Unattributed'):
            continue
        merged = {}
        for record in records:
            if record['kind'] != 'attempt':
                continue
            key = record.get('attempt_id') or record['event_id']
            if key not in merged:
                merged[key] = dict(record)
            else:
                # A late start never erases terminal usage/status evidence.
                previous = merged[key]
                terminal = record.get('status') != 'started'
                merged[key] = {**previous, **{k:v for k,v in record.items() if v is not None and (terminal or k not in previous)}}
                merged[key]['timestamp'] = min(previous['timestamp'],record['timestamp'], key=_timestamp)
        selected = [r for r in merged.values() if in_range(_timestamp(r['timestamp']))]
        visible_records = [r for r in records if in_range(_timestamp(r['timestamp']))]
        if not selected and not visible_records:
            continue
        issues = set()
        if not routes: issues.add('missing_route_evidence')
        if not merged and metadata.get('outcome') not in ('disabled','abstained','unrouted/default'):
            issues.add('missing_attempt_evidence')
        if metadata.get('outcome') in ('failed','error','rejected','fallback'):
            issues.add('routing_failure')
        if metadata.get('outcome') in ('abstained','unrouted/default') and metadata.get('reason_code') not in (
                'low_confidence','low Jev confidence','disabled','routing disabled'):
            issues.add('routing_failure')
        attempts = []
        for request in selected:
            stamp = _timestamp(request['timestamp'])
            pos = bisect_right(route_times, stamp)-1
            historical = routes[pos] if pos >= 0 else {}
            usage = request.get('usage')
            known = _known_usage(usage)
            model = request.get('reported_model') or request.get('requested_model')
            provider_cost = _estimate(model, usage, pricing)
            override_pos = bisect_right(override_times,stamp)-1
            override = overrides[override_pos] if override_pos>=0 else {}
            manual = bool(request.get('manual_override') or historical.get('manual_override') or override.get('manual_override'))
            default = _estimate(historical.get('default_model'), usage, pricing)
            eligible = historical.get('eligible_models') or []
            eligible_costs = [_estimate(e.get('model'),usage,pricing) for e in eligible]
            valid_eligible = bool(eligible and historical.get('eligibility_source') and
                all(type(e.get('capability')) is int for e in eligible) and
                all(_has_price(e.get('model'), pricing) for e in eligible))
            comparable = bool(known and provider_cost is not None and default is not None and valid_eligible and not manual)
            if not known: issues.add('missing_usage')
            if known and not _known_cache(usage): issues.add('missing_usage_breakdown')
            if known and not _has_price(model, pricing): issues.add('missing_price')
            if not historical: issues.add('missing_route_evidence')
            baseline_expected = not (historical.get('outcome') == 'disabled' or (
                historical.get('outcome') in ('abstained', 'unrouted/default') and
                historical.get('reason_code') in ('low_confidence', 'low Jev confidence')))
            if known and not manual and baseline_expected and (not _has_price(historical.get('default_model'), pricing) or not valid_eligible):
                issues.add('missing_baseline_evidence')
            if request.get('status') in ('failed','rejected'): issues.add('provider_failure')
            if request.get('fallback'): issues.add('fallback')
            if request.get('status') not in ('completed','failed','rejected'): issues.add('incomplete_attempt')
            median_cost = median(eligible_costs) if comparable else None
            strongest = None
            if comparable:
                highest = max(e['capability'] for e in eligible)
                # A tie selects an actual model deterministically, never a price-ranked tier.
                strongest_model = min(e['model'] for e in eligible if e['capability']==highest)
                strongest = _estimate(strongest_model,usage,pricing)
            a = {k:request.get(k) for k in ('attempt_id','timestamp','requested_model','reported_model',
                'requested_effort','reported_effort','status','usage','fallback','provider')}
            a['usage'] = {k:usage.get(k) for k in ('input_tokens','cached_input_tokens','output_tokens','reasoning_tokens','source')} if isinstance(usage,dict) else None
            a.update(default_model=historical.get('default_model'),
                policy_version=historical.get('policy_version'),
                eligibility_source=historical.get('eligibility_source'),
                manual_override=manual, provider_cost_usd=provider_cost,
                default_cost_usd=default if comparable else None, routed_cost_usd=provider_cost if comparable else None,
                median_cost_usd=median_cost, strongest_cost_usd=strongest,
                provider_difference_usd=default-provider_cost if comparable else None,
                billed_usd=_charge(request.get('billed_usd')), known_usage=known, comparable=comparable,
                missing_price=known and not _has_price(model, pricing),
                missing_usage_breakdown=known and not _known_cache(usage))
            attempts.append(a)
            all_attempts.append(a)
            day_entries[stamp.astimezone(zone).date().isoformat()].append((conv,a))
        overhead_records = [r for r in records if r['kind']=='overhead' and in_range(_timestamp(r['timestamp']))]
        charges = [_charge(r.get('overhead_usd')) if r.get('overhead_source') else None for r in overhead_records]
        overhead_cost = _sum(charges) if charges and all(c is not None for c in charges) else None
        # All route selections must be covered; one overhead record is one actual charge.
        expected_routes = sum(in_range(t) for t in route_times)
        overhead_complete = overhead_cost is not None and len(charges)>=max(1,expected_routes)
        complete = bool(attempts and all(a['comparable'] for a in attempts) and overhead_complete)
        for record, charge in zip(overhead_records,charges):
            overhead_by_day[_timestamp(record['timestamp']).astimezone(zone).date().isoformat()].append(charge)
        difference = _sum(a['provider_difference_usd'] for a in attempts)
        row = {k:metadata.get(k) for k in ('default_model','selected_model','requested_effort','model_tier',
                'effort_tier','reason_code','outcome','policy_version','latency_ms','eligible_models','eligibility_source')}
        row['eligible_models'] = [{k:e.get(k) for k in ('model','capability')} for e in (metadata.get('eligible_models') or [])]
        row.update(id=conv,timestamp=min((r['timestamp'] for r in visible_records),key=_timestamp),
            project=attribution,attempts=attempts,review_reasons=sorted(issues),
            manual_override=any(a['manual_override'] for a in attempts),
            provider_cost_usd=_sum(a['provider_cost_usd'] for a in attempts),
            default_cost_usd=_sum(a['default_cost_usd'] for a in attempts),
            provider_difference_usd=difference,overhead_usd=overhead_cost,
            net_savings_usd=difference-overhead_cost if complete else None,
            overhead_available=overhead_complete,request_count=len(attempts))
        rows.append(row)
    comparison = [a for a in all_attempts if a['comparable']]
    overhead = _sum(r['overhead_usd'] for r in rows)
    overhead_available = bool(rows and all(r['overhead_available'] for r in rows))
    net = _sum(r['net_savings_usd'] for r in rows) if rows and all(r['net_savings_usd'] is not None for r in rows) else None
    summary = dict(conversation_count=len(rows), request_count=len(all_attempts),
        review_count=sum(bool(r['review_reasons']) for r in rows),
        provider_cost_usd=_sum(a['provider_cost_usd'] for a in all_attempts),
        billed_cost_usd=_sum(a['billed_usd'] for a in all_attempts),
        billed_requests=sum(a['billed_usd'] is not None for a in all_attempts),
        overhead_usd=overhead, overhead_available=overhead_available, net_savings_usd=net,
        comparison_excluded_requests=len(all_attempts)-len(comparison),
        routed_count=sum(r['outcome']=='routed' for r in rows),
        abstained_count=sum(r['outcome'] in ('abstained','unrouted/default') and 'routing_failure' not in r['review_reasons'] for r in rows),
        disabled_count=sum(r['outcome']=='disabled' for r in rows),
        routing_failed_count=sum('routing_failure' in r['review_reasons'] for r in rows),
        failed_count=sum(bool(set(r['review_reasons']) & {'routing_failure','provider_failure'}) for r in rows),
        manual_count=sum(r['manual_override'] for r in rows))
    for key in ('default_cost_usd','routed_cost_usd','median_cost_usd','strongest_cost_usd','provider_difference_usd'):
        summary[key]=_sum(a[key] for a in comparison)
    coverage = dict(total_requests=len(all_attempts),known_usage_requests=sum(a['known_usage'] for a in all_attempts),
        priced_requests=sum(a['provider_cost_usd'] is not None for a in all_attempts),comparison_requests=len(comparison),
        missing_usage_requests=sum(not a['known_usage'] for a in all_attempts),
        missing_price_requests=sum(a['missing_price'] for a in all_attempts),
        missing_usage_breakdown_requests=sum(a['missing_usage_breakdown'] for a in all_attempts),
        manual_requests=sum(a['manual_override'] for a in all_attempts))
    series=[]
    for day, entries in sorted(day_entries.items()):
        day_row=dict(date=day,conversation_count=len({c for c,a in entries}),request_count=len(entries))
        for key in ('default_cost_usd','routed_cost_usd','provider_difference_usd'):
            day_row[key]=_sum(a[key] for c,a in entries)
        day_row['overhead_usd']=_sum(overhead_by_day.get(day,[]))
        day_row['net_savings_usd']=None  # Daily allocation of a session charge is not fabricated.
        series.append(day_row)
    # Conversation bars partition the selected conversations once, while request
    # bars count each actual outgoing attempt. Provider aliases affect pricing,
    # not the requested-model chart. Route-only chats have no request evidence.
    model_groups=defaultdict(lambda: dict(conversations=0, requests=0))
    project_groups=defaultdict(list)
    for row in rows:
        project_groups[row['project'] or 'Unattributed'].append(row)
        latest = max(row['attempts'], key=lambda a: _timestamp(a['timestamp']), default={})
        model_groups[latest.get('requested_model') or 'Unavailable']['conversations'] += 1
        for a in row['attempts']:
            model_groups[a['requested_model'] or 'Unavailable']['requests'] += 1
    models=[dict(model=m,**counts) for m,counts in sorted(model_groups.items())]
    projects=[dict(project=p,conversations=len(items),requests=sum(r['request_count'] for r in items),
        provider_cost_usd=_sum(r['provider_cost_usd'] for r in items),
        provider_difference_usd=_sum(r['provider_difference_usd'] for r in items)) for p,items in sorted(project_groups.items())]
    result=dict(mode='live',currency='USD',summary=summary,coverage=coverage,conversations=rows,series=series,
        models=models,projects=projects,project_options=sorted(options),pricing=pricing,limitations=LIMITATIONS,
        as_of=ordered[-1]['timestamp'] if ordered else None,filters=dict(start=start,end=end,timezone=timezone,project=project))
    # Keep Decimal throughout calculation; convert only at the JSON boundary.
    return json.loads(json.dumps(result,default=lambda v: float(v) if isinstance(v,Decimal) else str(v)))
