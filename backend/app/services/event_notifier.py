from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field


class GameEventNotifier:
    def __init__(self):
        self._lock = threading.Lock()
        self._subscriptions: dict[str, set[GameEventSubscription]] = {}

    def subscribe(self, game_id: str) -> "GameEventSubscription":
        subscription = GameEventSubscription(
            game_id=game_id,
            notifier=self,
            loop=asyncio.get_running_loop(),
        )
        with self._lock:
            self._subscriptions.setdefault(game_id, set()).add(subscription)
        return subscription

    def notify(self, game_id: str) -> None:
        with self._lock:
            subscriptions = tuple(self._subscriptions.get(game_id, ()))
        for subscription in subscriptions:
            subscription.notify()

    def unsubscribe(self, subscription: "GameEventSubscription") -> None:
        with self._lock:
            subscriptions = self._subscriptions.get(subscription.game_id)
            if subscriptions is None:
                return
            subscriptions.discard(subscription)
            if not subscriptions:
                self._subscriptions.pop(subscription.game_id, None)

    def subscriber_count(self, game_id: str) -> int:
        with self._lock:
            return len(self._subscriptions.get(game_id, ()))


@dataclass(eq=False)
class GameEventSubscription:
    game_id: str
    notifier: GameEventNotifier
    loop: asyncio.AbstractEventLoop
    _event: asyncio.Event = field(default_factory=asyncio.Event)
    _closed: bool = False

    async def wait(self, timeout: float) -> bool:
        if self._closed:
            return False
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        self._event.clear()
        return True

    def notify(self) -> None:
        if self._closed or self.loop.is_closed():
            return
        self.loop.call_soon_threadsafe(self._event.set)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.notifier.unsubscribe(self)
