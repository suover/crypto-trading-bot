from scripts import run_mock_order_retry_worker


def test_run_worker_exits_when_lock_is_not_acquired(
    monkeypatch,
    capsys,
) -> None:
    lock_instances = []

    class RejectingLock:
        def __init__(
            self,
            lock_key: int,
        ) -> None:
            self.lock_key = lock_key
            self.release_count = 0
            lock_instances.append(self)

        def acquire(self) -> bool:
            return False

        def release(self) -> None:
            self.release_count += 1

    monkeypatch.setattr(
        run_mock_order_retry_worker,
        "PostgresAdvisoryLock",
        RejectingLock,
    )

    run_mock_order_retry_worker.run_worker(
        interval_seconds=60,
        limit=100,
        once=True,
    )

    captured = capsys.readouterr()

    assert len(lock_instances) == 1
    assert lock_instances[0].release_count == 0
    assert (
        "Another mock order retry worker is already "
        "running. Worker will exit." in captured.out
    )


def test_run_worker_releases_lock_after_once_cycle(
    monkeypatch,
    capsys,
) -> None:
    lock_instances = []
    retry_calls = []

    class AcquiredLock:
        def __init__(
            self,
            lock_key: int,
        ) -> None:
            self.lock_key = lock_key
            self.release_count = 0
            lock_instances.append(self)

        def acquire(self) -> bool:
            return True

        def release(self) -> None:
            self.release_count += 1

    def fake_run_retry_cycle(
        limit: int,
    ) -> None:
        retry_calls.append(limit)

    monkeypatch.setattr(
        run_mock_order_retry_worker,
        "PostgresAdvisoryLock",
        AcquiredLock,
    )

    monkeypatch.setattr(
        run_mock_order_retry_worker,
        "run_retry_cycle",
        fake_run_retry_cycle,
    )

    run_mock_order_retry_worker.run_worker(
        interval_seconds=60,
        limit=100,
        once=True,
    )

    captured = capsys.readouterr()

    assert retry_calls == [100]
    assert len(lock_instances) == 1
    assert lock_instances[0].release_count == 1

    assert "Mock order retry worker lock acquired." in captured.out
    assert "Mock order retry worker lock released." in captured.out
