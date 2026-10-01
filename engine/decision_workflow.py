"""Decision-owned cross-app outcomes over the shared typed live/replay recorder."""
import asyncio
import hashlib
import json
import time
from pydantic import BaseModel, ConfigDict, Field, model_validator
from decision_browser import InputSlot
from decision import safe_decision_error


class WorkflowOutcome(BaseModel):
    model_config = ConfigDict(extra='forbid')
    value: str = Field(min_length=1, max_length=500)
    record: str | None = Field(default=None, description=(
        'Captured record variable only for text inside that record row. '
        'Use null for page-wide notices or confirmation messages outside record rows.'))


class WorkflowMilestone(BaseModel):
    model_config = ConfigDict(extra='forbid')
    app: str
    actor: str
    goal: str = Field(min_length=1, max_length=600)
    inputs: list[str] = Field(default_factory=list, max_length=12)
    captures: list[str] = Field(default_factory=list, max_length=2)
    outcomes: list[WorkflowOutcome] = Field(min_length=1, max_length=8)


class WorkflowContract(BaseModel):
    model_config = ConfigDict(extra='forbid')
    goal: str = Field(min_length=1, max_length=1500)
    inputs: list[InputSlot] = Field(default_factory=list, max_length=20)
    milestones: list[WorkflowMilestone] = Field(min_length=2, max_length=12)

    @model_validator(mode='after')
    def valid(self):
        names = [s.name for s in self.inputs]
        if len(names) != len(set(names)):
            raise ValueError('Duplicate input slots')
        captures = set()
        for milestone in self.milestones:
            if any(s not in names for s in milestone.inputs):
                raise ValueError('Unknown input slot')
            for name in milestone.captures:
                if not name or name in captures:
                    raise ValueError('Duplicate or empty capture')
                captures.add(name)
            if any(o.record and o.record not in captures for o in milestone.outcomes):
                raise ValueError('Undefined captured relationship')
        return self


def options(observation, milestone, inputs, captures, bindings, assignments=None):
    """DOM-derived choices only; contract contains no refs, selectors or actions."""
    current = observation['app'], observation['actor']
    assignments = assignments or {}
    choices = {'stop': {'kind': 'stop'}, 'wait': {'kind': 'wait'}}
    if matched_outcomes(observation, milestone, captures) is not None:
        choices['check'] = {'kind': 'check'}
        # Commit this required readback before any action can invalidate it.
        return choices
    for app, actor in bindings:
        if (app, actor) != current and (app, actor) == (milestone.app, milestone.actor):
            choices[f'a{len(choices)}'] = {'kind': 'switch_app', 'app': app, 'actor': actor}
    if current != (milestone.app, milestone.actor):
        return choices
    for target in observation['targets']:
        if target.get('sensitive') or target.get('disabled'):
            continue
        base = {k: target.get(k) for k in ('ref', 'test_id', 'record')}
        label = target.get('label') or target.get('text') or target['test_id']
        if target.get('fillable'):
            for name in milestone.inputs:
                identity = (target['test_id'], target.get('record'))
                if name in assignments and assignments[name] != identity:
                    continue
                if any(slot != name and bound == identity for slot, bound in assignments.items()):
                    continue
                if target.get('value') != inputs[name]:
                    choices[f'a{len(choices)}'] = {**base, 'kind': 'fill', 'slot': name, 'label': label}
        if target.get('tag') in {'button', 'a'}:
            choices[f'a{len(choices)}'] = {**base, 'kind': 'click', 'label': label}
        if target.get('tag') == 'output' and target.get('text', '').strip() and not target.get('record'):
            for name in milestone.captures:
                if name not in captures:
                    choices[f'a{len(choices)}'] = {**base, 'kind': 'capture', 'capture': name, 'label': label}
    if len(choices) > 240:
        raise ValueError('Candidate overflow')
    return choices


def matched_outcomes(observation, milestone, captures):
    if (observation['app'], observation['actor']) != (milestone.app, milestone.actor):
        return None
    if any(name not in captures for name in milestone.captures):
        return None
    result = []
    for outcome in milestone.outcomes:
        matches = [t for t in observation['targets'] if not t.get('fillable') and
                   t.get('text', '').strip() == outcome.value and t.get('record') == outcome.record]
        if len(matches) != 1:
            return None
        result.append((matches[0]['ref'], outcome.value))
    return result


def input_readiness(observation, milestone, inputs, assignments):
    """Report current DOM readback separately from future saved-record outcomes."""
    matched = []
    for name in milestone.inputs:
        identity = assignments.get(name)
        targets = [t for t in observation['targets'] if t.get('fillable') and
                   (t.get('test_id'), t.get('record')) == identity and t.get('value') == inputs[name]]
        if len(targets) == 1:
            matched.append(name)
    return {'matched_slots': matched, 'pending_slots': [n for n in milestone.inputs if n not in matched],
            'all_declared_inputs_match': len(matched) == len(milestone.inputs)}


async def execute_workflow(contract, recorder, bro, decider, authorize, emit=lambda e: None,
                           max_decisions=80, timeout=240):
    bindings = [(a['id'], actor) for a in recorder.project['apps'] for actor in a.get('actors', [])]
    if any((m.app, m.actor) not in bindings for m in contract.milestones):
        return {'ok': False, 'status': 'unregistered_binding', 'dispatched': False}
    inputs = {s.name: s.value for s in contract.inputs}
    current = (contract.milestones[0].app, contract.milestones[0].actor)
    stage, trace, attempts, confirms = 0, [], {}, set()
    assignments = {}
    pending = None
    deadline = time.monotonic() + timeout
    def result(status, **extra):
        return {'ok': status == 'outcomes_observed', 'status': status, 'verified': False,
                'milestones_completed': stage, 'trace': trace, **extra}
    for turn in range(max_decisions):
        if time.monotonic() >= deadline:
            return result('budget_exhausted')
        milestone = contract.milestones[stage]
        observation = await bro(recorder.observe, *current)
        if observation.get('truncated'):
            return result('candidate_overflow')
        stage_assignments = assignments.setdefault(stage, {})
        choices = options(observation, milestone, inputs, recorder.captures, bindings, stage_assignments)
        stable_targets = [{k: v for k, v in t.items() if k != 'ref'} for t in observation['targets']]
        state_hash = hashlib.sha256(json.dumps([current, stable_targets], sort_keys=True).encode()).hexdigest()
        def identity(action):
            return tuple(action.get(k) for k in ('kind', 'app', 'actor', 'test_id', 'record', 'slot', 'capture'))
        if pending and state_hash != pending[0]:
            return result('confirmation_state_changed')
        choices = {key: action for key, action in choices.items() if action['kind'] == 'stop' or
                   attempts.get((stage, state_hash, identity(action)), 0) < (8 if action['kind'] == 'wait' else 2)}
        if len(choices) < 2:
            return result('stalled')
        matches = matched_outcomes(observation, milestone, recorder.captures)
        state = {'goal': contract.goal, 'milestone': milestone.model_dump(), 'current_app': current[0],
                 'current_actor': current[1], 'outcomes_ready': matches is not None,
                 'authorized_effects': 'The user authorizes the stated synthetic workflow in these registered apps and roles. Operate only on the captured record. Authentication is managed by the host.',
                 'pending_confirmation': ({'action_identity': pending[1],
                     'reason': 'No action was dispatched. Fresh observation confirms unchanged state and target. Reassess all alternatives. The same click requires confidence >= 0.8; stop if uncertain.'}
                     if pending else None),
                 'captured_names': list(recorder.captures),
                 'input_readback': input_readiness(observation, milestone, inputs, stage_assignments),
                 'pending_generated_captures': [name for name in milestone.captures if name not in recorder.captures],
                 'assigned_input_fields': stage_assignments,
                 'input_slots': [{'name': name, 'value_kind': 'number' if value.replace('.', '', 1).isdigit() else 'text'}
                                 for name, value in inputs.items() if name in milestone.inputs],
                 'recent_actions': trace[-6:],
                 'instructions': 'Fill each named slot into its semantically matching field, then submit when the required fields are ready. input_readback reports actual current DOM values matching assigned inputs. Saved-record outcomes and generated captures can only become available after submission; their absence before submission is expected. Preserve fields already holding their assigned slot. Do not cycle through different values in one field. Check when outcomes_ready is true. Captures are generated record identities, never editable inputs.',
                 'targets': [{k: v for k, v in t.items() if k not in {'ref', 'value', 'sensitive'}} |
                             {'matches_inputs': [n for n, value in inputs.items() if t.get('value') == value]}
                             for t in observation['targets']]}
        criteria = {key: json.dumps({k: v for k, v in a.items() if k != 'ref'}) for key, a in choices.items()}
        try:
            decision = await asyncio.wait_for(asyncio.to_thread(decider.choose, state, criteria),
                                             timeout=max(.01, deadline - time.monotonic()))
        except Exception as exc:
            return result('decision_error', terminal=True, error_type=type(exc).__name__,
                          **safe_decision_error(exc))
        action = choices.get(decision.choice)
        if action is None:
            return result('invalid_decision')
        kind = action['kind']
        emit({'event': 'decision_workflow_decision', 'step': turn + 1, 'milestone': stage,
              'app': current[0], 'actor': current[1], 'kind': kind, 'confidence': decision.confidence,
              'candidate_count': len(choices), 'target_test_id': action.get('test_id'), 'slot': action.get('slot'),
              'alternatives': [{**{k: v for k, v in choices[key].items() if k != 'ref'}, 'probability': probability}
                               for key, probability in sorted((decision.probabilities or {}).items(), key=lambda item: -item[1])[:5]
                               if key in choices]})
        if kind == 'stop':
            return result('decision_handoff')
        key = (stage, state_hash, identity(action))
        if pending:
            if identity(action) != pending[1] or decision.confidence is None or decision.confidence < .8:
                return result('decision_handoff')
            pending = None
        elif kind == 'click' and (decision.confidence is None or decision.confidence < .8):
            if key in confirms:
                return result('decision_handoff')
            confirms.add(key)
            pending = (state_hash, identity(action))
            emit({'event': 'decision_workflow_confirmation', 'status': 'requested', 'milestone': stage})
            continue
        attempts[key] = attempts.get(key, 0) + 1
        ok = True
        try:
            if kind == 'switch_app':
                current = action['app'], action['actor']
            elif kind == 'wait':
                await asyncio.sleep(.5)
            elif kind == 'check':
                if matches is None:
                    ok = False
                else:
                    start = len(recorder.steps)
                    try:
                        for ref, value in matches:
                            await bro(recorder.act, 'expect_text', ref, value, preserve_refs=True)
                    except Exception:
                        del recorder.steps[start:]
                        ok = False
                    if ok:
                        stage += 1
            elif kind == 'fill':
                value = await authorize(action['ref'], inputs[action['slot']])
                await bro(recorder.act, 'fill', action['ref'], value)
                stage_assignments[action['slot']] = (action['test_id'], action.get('record'))
            elif kind == 'capture':
                await bro(recorder.act, 'capture', action['ref'], capture=action['capture'])
            elif kind == 'click':
                await bro(recorder.act, 'click', action['ref'])
        except Exception as exc:
            return result('action_uncertain', error_type=type(exc).__name__, tainted=recorder.tainted,
                          action_kind=kind, target_test_id=action.get('test_id'))
        row = {'step': turn + 1, 'kind': kind, 'ok': ok, 'milestone': stage,
               'app': current[0], 'actor': current[1], 'target_test_id': action.get('test_id'), 'slot': action.get('slot')}
        trace.append(row)
        emit({'event': 'decision_browser_step', 'scope': 'multi_app', **row})
        if stage == len(contract.milestones):
            return result('outcomes_observed')
    return result('budget_exhausted')
