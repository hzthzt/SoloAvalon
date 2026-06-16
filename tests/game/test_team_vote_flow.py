import unittest

from backend.app.game.models import Phase, Vote
from backend.app.game.rules import (
    InvalidActionError,
    cast_vote,
    create_five_player_game,
    finalize_vote,
    propose_team,
    record_speech,
)


def team_for_current_mission(state):
    return tuple(player.id for player in state.players[: state.missions[state.current_round - 1].team_size])


def complete_speeches(state):
    for player_id in state.speech_order:
        state = record_speech(state, player_id, f"{player_id} says something")
    return state


def vote_all(state, votes):
    for player in state.players:
        state = cast_vote(state, player.id, votes[player.id])
    return finalize_vote(state)


class TeamVoteFlowTests(unittest.TestCase):
    def test_only_current_leader_can_propose_team(self):
        state = create_five_player_game(seed=1)
        leader = state.players[state.leader_index]
        non_leader = state.players[1]

        with self.assertRaises(InvalidActionError):
            propose_team(state, non_leader.id, team_for_current_mission(state))

        proposed = propose_team(state, leader.id, team_for_current_mission(state))

        self.assertEqual(proposed.phase, Phase.SPEECH)
        self.assertEqual(proposed.proposed_team, team_for_current_mission(state))

    def test_team_size_must_match_current_mission(self):
        state = create_five_player_game(seed=2)
        leader = state.players[state.leader_index]
        three_player_team = tuple(player.id for player in state.players[:3])

        with self.assertRaises(InvalidActionError):
            propose_team(state, leader.id, three_player_team)

    def test_speeches_follow_fixed_order_from_leader_then_open_voting(self):
        state = create_five_player_game(seed=3)
        state = _reject_once(state)
        leader = state.players[state.leader_index]
        state = propose_team(state, leader.id, team_for_current_mission(state))

        self.assertEqual(
            state.speech_order,
            ("player_2", "player_3", "player_4", "player_5", "player_1"),
        )
        with self.assertRaises(InvalidActionError):
            record_speech(state, "player_3", "jumping the queue")

        for player_id in state.speech_order[:-1]:
            state = record_speech(state, player_id, f"{player_id} speaking")
            self.assertEqual(state.phase, Phase.SPEECH)

        state = record_speech(state, state.speech_order[-1], "last speaker")

        self.assertEqual(state.phase, Phase.VOTING)

    def test_majority_approval_advances_to_quest_action(self):
        state = create_five_player_game(seed=4)
        leader = state.players[state.leader_index]
        state = propose_team(state, leader.id, team_for_current_mission(state))
        state = complete_speeches(state)
        votes = {
            "player_1": Vote.APPROVE,
            "player_2": Vote.APPROVE,
            "player_3": Vote.APPROVE,
            "player_4": Vote.REJECT,
            "player_5": Vote.REJECT,
        }

        state = vote_all(state, votes)

        self.assertEqual(state.phase, Phase.QUEST)
        self.assertEqual(state.failed_team_votes, 0)

    def test_rejected_vote_rotates_leader_and_counts_failed_vote(self):
        state = _reject_once(create_five_player_game(seed=5))

        self.assertEqual(state.phase, Phase.TEAM_PROPOSAL)
        self.assertEqual(state.failed_team_votes, 1)
        self.assertEqual(state.leader_index, 1)
        self.assertEqual(state.proposed_team, ())

    def test_fifth_rejected_vote_triggers_forced_team_that_bypasses_voting(self):
        state = create_five_player_game(seed=6)
        for _ in range(5):
            state = _reject_once(state)

        self.assertEqual(state.phase, Phase.TEAM_PROPOSAL)
        self.assertEqual(state.failed_team_votes, 5)
        self.assertTrue(state.forced_team)

        leader = state.players[state.leader_index]
        state = propose_team(state, leader.id, team_for_current_mission(state))

        self.assertEqual(state.phase, Phase.QUEST)
        self.assertEqual(state.proposed_team, team_for_current_mission(state))


def _reject_once(state):
    leader = state.players[state.leader_index]
    state = propose_team(state, leader.id, team_for_current_mission(state))
    state = complete_speeches(state)
    return vote_all(
        state,
        {
            player.id: Vote.REJECT
            for player in state.players
        },
    )

