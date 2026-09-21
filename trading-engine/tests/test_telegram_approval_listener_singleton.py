from scripts import run_telegram_approval_listener


def test_listener_exits_when_lock_is_not_acquired(
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
        run_telegram_approval_listener,
        "PostgresAdvisoryLock",
        RejectingLock,
    )

    run_telegram_approval_listener.run_telegram_approval_listener()

    captured = capsys.readouterr()

    assert len(lock_instances) == 1
    assert lock_instances[0].release_count == 0
    assert (
        "Another Telegram approval listener is "
        "already running. Listener will exit." in captured.out
    )


def test_listener_releases_lock_when_stopped_by_keyboard_interrupt(
    monkeypatch,
    capsys,
) -> None:
    lock_instances = []

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

    class InterruptingTelegramClient:
        def get_updates(
            self,
            offset: int | None = None,
            timeout: int = 30,
        ) -> list[dict]:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        run_telegram_approval_listener,
        "PostgresAdvisoryLock",
        AcquiredLock,
    )
    monkeypatch.setattr(
        run_telegram_approval_listener,
        "TelegramClient",
        InterruptingTelegramClient,
    )
    monkeypatch.setattr(
        run_telegram_approval_listener,
        "expire_pending_approval_requests",
        lambda: 0,
    )

    run_telegram_approval_listener.run_telegram_approval_listener()

    captured = capsys.readouterr()

    assert len(lock_instances) == 1
    assert lock_instances[0].release_count == 1
    assert "Telegram approval listener lock acquired." in captured.out
    assert "Telegram approval listener started." in captured.out
    assert "Telegram approval listener stopped." in captured.out
    assert "Telegram approval listener lock released." in captured.out
