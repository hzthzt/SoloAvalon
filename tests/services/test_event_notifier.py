import asyncio
import unittest

from backend.app.services.event_notifier import GameEventNotifier


class GameEventNotifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_notify_wakes_subscription_for_matching_game(self):
        notifier = GameEventNotifier()
        subscription = notifier.subscribe("game_1")
        try:
            waiter = asyncio.create_task(subscription.wait(timeout=0.5))

            notifier.notify("game_1")

            self.assertTrue(await asyncio.wait_for(waiter, timeout=0.5))
        finally:
            subscription.close()

    async def test_notify_does_not_wake_other_games(self):
        notifier = GameEventNotifier()
        subscription = notifier.subscribe("game_1")
        try:
            notifier.notify("game_2")

            self.assertFalse(await subscription.wait(timeout=0.01))
        finally:
            subscription.close()

    async def test_close_unregisters_subscription(self):
        notifier = GameEventNotifier()
        subscription = notifier.subscribe("game_1")

        self.assertEqual(notifier.subscriber_count("game_1"), 1)

        subscription.close()
        notifier.notify("game_1")

        self.assertEqual(notifier.subscriber_count("game_1"), 0)

