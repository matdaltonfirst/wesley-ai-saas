def determine_priority(timeline, request_type, deliverables,
                       key_info='', special_notes=''):
    template_keywords = ['template', 'simple', 'basic',
                         'standard', 'existing format']
    combined = f"{key_info} {special_notes}".lower()
    is_template = any(k in combined for k in template_keywords)
    is_video = request_type == 'video'
    is_complex = len(deliverables) > 2 or is_video

    if timeline == 'this_week':
        return 'red', ('2-3 days' if is_video else '1-2 days')
    if timeline == '2_4_weeks':
        return 'yellow', ('5-7 days' if is_complex else '3-5 days')
    if timeline == '1_plus_month' and (is_template or len(deliverables) == 1):
        return 'blue', '1-2 weeks'
    return 'green', ('7-10 days' if is_complex else '5-7 days')


def determine_tier(target_audience):
    return {'community': 1, 'church_members': 2,
            'small_group': 3}.get(target_audience, 2)


def generate_triage_explanation(anthropic_client, request_data,
                                priority, tier, est_completion):
    tier_descriptions = {
        1: 'Tier 1 - Manual (Communications Director creates from scratch for external/community audience)',
        2: 'Tier 2 - AI-Assisted (AI generates initial draft, designer finishes for church members)',
        3: 'Tier 3 - Full AI (Quick AI-generated turnaround for small group or internal use)',
    }
    prompt = f"""You are a communications triage expert for a church. Provide a 2-3 sentence plain-English explanation for this priority assignment. Keep it helpful and clear for church staff.

Request: {request_data['event_name']}
Audience: {request_data['target_audience'].replace('_', ' ')}
Timeline: {request_data['timeline'].replace('_', ' ')}
Deliverables: {', '.join(request_data['deliverables'])}
Notes: {request_data.get('special_notes') or 'None'}

Assigned Priority: {priority.upper()} (Est. completion: {est_completion})
Assigned Tier: {tier_descriptions[tier]}

Explain why this priority and tier were assigned."""

    response = anthropic_client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text
