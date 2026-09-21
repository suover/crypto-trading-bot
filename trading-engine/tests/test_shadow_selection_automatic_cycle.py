from types import SimpleNamespace

from scripts.run_recommendation_outcome_worker import run_shadow_selection_cycle


class FakeSession:
    def __init__(self, *, candidate_ids=()):
        self.candidate_ids = candidate_ids
        self.commits = 0
        self.rollbacks = 0
        self.statement = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def scalars(self, statement):
        self.statement = statement
        return self.candidate_ids

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def result(status, *, created=0, evaluation_statuses=(), database_write=False):
    return SimpleNamespace(
        status=status,
        safe_reason="fixture reason",
        database_write=database_write,
        created_evaluation_count=created,
        evaluations=tuple(
            SimpleNamespace(evaluation_status=value) for value in evaluation_statuses
        ),
    )


def configure_cycle(monkeypatch, candidate_ids, outcomes, *, after_enumeration=None):
    enumeration = FakeSession(candidate_ids=candidate_ids)
    candidate_sessions = []

    def session_factory():
        if not candidate_sessions and enumeration.statement is None:
            return enumeration
        session = FakeSession()
        candidate_sessions.append(session)
        return session

    class FakeService:
        def __init__(self, session):
            self.session = session

        def evaluate(self, *, candidate_id):
            if after_enumeration is not None:
                after_enumeration(candidate_id)
            value = outcomes[candidate_id]
            if isinstance(value, Exception):
                raise value
            return value

    monkeypatch.setattr(
        "crypto_trading_bot.services.shadow_policy_evaluation_service.ShadowPolicyEvaluationService",
        FakeService,
    )
    return session_factory, enumeration, candidate_sessions


def test_cycle_enumerates_enrollments_in_id_order_and_freezes_target_set(monkeypatch):
    from crypto_trading_bot.services.shadow_policy_evaluation_service import (
        NO_NEW_SHADOW_EVALUATIONS,
    )

    processed = []
    live_enrollments = [10, 3, 7]
    outcomes = {
        candidate_id: result(NO_NEW_SHADOW_EVALUATIONS)
        for candidate_id in live_enrollments
    }

    def after_enumeration(candidate_id):
        processed.append(candidate_id)
        live_enrollments.append(99)

    factory, enumeration, _ = configure_cycle(
        monkeypatch,
        tuple(live_enrollments),
        outcomes,
        after_enumeration=after_enumeration,
    )
    cycle = run_shadow_selection_cycle(factory)
    sql = str(enumeration.statement)
    assert "shadow_policy_enrollments" in sql
    assert "ORDER BY shadow_policy_enrollments.id" in sql
    assert processed == [10, 3, 7]
    assert cycle.enrollment_count == 3
    assert cycle.no_new_evaluation_candidate_count == 3


def test_candidate_transactions_commit_success_and_isolate_invalid_and_exception(
    monkeypatch,
):
    from crypto_trading_bot.services.shadow_policy_evaluation_service import (
        CONTEXT_MISMATCH,
        INVALID_SHADOW_EVALUATION,
        SUCCESS,
    )

    factory, enumeration, sessions = configure_cycle(
        monkeypatch,
        (3, 5, 8, 13),
        {
            3: result(
                SUCCESS,
                created=1,
                evaluation_statuses=(SUCCESS,),
                database_write=True,
            ),
            5: result(INVALID_SHADOW_EVALUATION),
            8: RuntimeError("fixture failure"),
            13: result(
                SUCCESS,
                created=1,
                evaluation_statuses=(CONTEXT_MISMATCH,),
                database_write=True,
            ),
        },
    )
    cycle = run_shadow_selection_cycle(factory)
    assert enumeration.rollbacks == 1
    assert [session.commits for session in sessions] == [1, 0, 0, 1]
    assert [session.rollbacks for session in sessions] == [0, 1, 1, 0]
    assert cycle.processed_candidate_count == 4
    assert cycle.success_candidate_count == 2
    assert cycle.invalid_candidate_count == 1
    assert cycle.exception_candidate_count == 1
    assert cycle.created_evaluation_count == 2
    assert cycle.success_evaluation_count == 1
    assert cycle.context_mismatch_count == 1


def test_no_enrollment_and_safe_noops_are_read_only(monkeypatch):
    from crypto_trading_bot.services.shadow_policy_evaluation_service import (
        NO_NEW_SHADOW_EVALUATIONS,
        NO_POST_ENROLLMENT_SNAPSHOTS,
        NO_SHADOW_ENROLLMENT,
    )

    factory, _, sessions = configure_cycle(
        monkeypatch,
        (1, 2, 3),
        {
            1: result(NO_POST_ENROLLMENT_SNAPSHOTS),
            2: result(NO_NEW_SHADOW_EVALUATIONS),
            3: result(NO_SHADOW_ENROLLMENT),
        },
    )
    cycle = run_shadow_selection_cycle(factory)
    assert [session.commits for session in sessions] == [0, 0, 0]
    assert [session.rollbacks for session in sessions] == [1, 1, 1]
    assert cycle.no_post_snapshot_candidate_count == 1
    assert cycle.no_new_evaluation_candidate_count == 1
    assert cycle.no_shadow_enrollment_candidate_count == 1
    assert cycle.exception_candidate_count == 0


def test_empty_enrollment_set_is_successful_and_does_not_create_candidate_session(
    monkeypatch,
):
    factory, enumeration, sessions = configure_cycle(monkeypatch, (), {})
    cycle = run_shadow_selection_cycle(factory)
    assert enumeration.rollbacks == 1
    assert sessions == []
    assert cycle.enrollment_count == 0
    assert cycle.processed_candidate_count == 0
    assert cycle.created_evaluation_count == 0


def test_result_contract_contradiction_rolls_back_and_continues(monkeypatch):
    from crypto_trading_bot.services.shadow_policy_evaluation_service import SUCCESS

    factory, _, sessions = configure_cycle(
        monkeypatch,
        (1, 2),
        {
            1: result(SUCCESS, created=0, database_write=False),
            2: result(
                SUCCESS,
                created=1,
                evaluation_statuses=(SUCCESS,),
                database_write=True,
            ),
        },
    )
    cycle = run_shadow_selection_cycle(factory)
    assert [session.commits for session in sessions] == [0, 1]
    assert [session.rollbacks for session in sessions] == [1, 0]
    assert cycle.exception_candidate_count == 1
    assert cycle.success_candidate_count == 1
