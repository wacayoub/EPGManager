"""Validate same-day local publication dependencies, not API-only fallbacks."""

def minutes(row):
    hour,minute=map(int,row['time'].split(':'))
    if not 0 <= hour < 24 or not 0 <= minute < 60:
        raise ValueError('Invalid daily target')
    return hour*60+minute


def validate(policy):
    sources=policy['sources']
    budget=policy['collection_budget_minutes']
    if not isinstance(budget,int) or budget <= 0:
        raise ValueError('Invalid collection budget')
    edges=[]
    for consumer,donors in policy['local_feed_dependencies'].items():
        for donor in donors:
            if consumer == donor or sources[consumer]['timezone'] != sources[donor]['timezone']:
                raise ValueError('Incompatible donor schedule')
            lead=minutes(sources[consumer])-minutes(sources[donor])
            if lead < budget:
                raise ValueError(f'{donor} must precede {consumer} by at least {budget} minutes (actual {lead})')
            edges.append({'donor':donor,'consumer':consumer,'lead_minutes':lead})
    for source,row in sources.items():
        if row['timezone'] != policy['postprocess']['timezone']:
            raise ValueError('Incompatible postprocessing timezone')
        if minutes(policy['postprocess'])-minutes(row) < budget:
            raise ValueError(source+' cannot finish its budget before postprocessing')
    return edges
