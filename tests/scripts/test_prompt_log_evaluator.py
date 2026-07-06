import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.prompt_log_evaluator import evaluate_prompt_log, evaluate_quality_gate


class PromptLogEvaluatorTests(unittest.TestCase):
    def test_counts_public_identity_contest_actions_without_private_text(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "玩家2一直替玩家3卸压，这关系不自然，我想听解释。",
                },
                {
                    "player_id": "player_2",
                    "message": "这车我暂时可认，失败再复盘。",
                },
                {
                    "player_id": "player_3",
                    "message": "玩家4急着排玩家5，理由是什么？",
                },
            ],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选。",
                        "public_message": "这车我暂时可认。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["speech_count"], 3)
        self.assertEqual(summary["identity_contest_action_count"], 2)
        self.assertEqual(summary["private_candidate_mention_count"], 1)
        self.assertEqual(summary["public_candidate_binding_count"], 0)

    def test_detects_public_candidate_binding_risk(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "玩家3和玩家6这两个候选关系先别打明。",
                }
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["public_candidate_binding_count"], 1)

    def test_counts_template_phrasing(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "责任链清晰，这条线我先记着，后续再看。",
                },
                {
                    "player_id": "player_2",
                    "message": "先跑一轮看看结果。",
                },
                {
                    "player_id": "player_3",
                    "message": "玩家2急着保玩家5，理由是什么？",
                },
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 2)
        self.assertEqual(
            [example["player_id"] for example in summary["template_phrase_examples"]],
            ["player_1", "player_2"],
        )

    def test_counts_vague_perspective_and_observation_phrasing(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "我想换我上去，多一个视角。",
                },
                {
                    "player_id": "player_2",
                    "message": "暂时不带玩家3和5，想先观察一轮。",
                },
                {
                    "player_id": "player_3",
                    "message": "我不反对上车，但我想换个组合看看反应。",
                },
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 3)

    def test_counts_vague_line_protection_as_template_phrasing(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "第一轮玩家1和玩家2成功了，这条线我先保着。",
                },
                {
                    "player_id": "player_2",
                    "message": "玩家2一直替玩家5卸压，这关系不自然。",
                },
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 1)
        self.assertEqual(summary["identity_contest_action_count"], 1)

    def test_counts_template_phrasing_in_decision_public_messages(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是梅林，先隐藏视角。",
                        "public_message": "第一轮先跑一轮看看结果，后续再说。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 1)
        self.assertEqual(
            summary["template_phrase_examples"],
            [
                {
                    "player_id": "",
                    "message": "第一轮先跑一轮看看结果，后续再说。",
                }
            ],
        )

    def test_counts_repeated_template_phrasing_from_different_players(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "先跑一轮看看结果。",
                },
                {
                    "player_id": "player_2",
                    "message": "先跑一轮看看结果。",
                },
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 2)

    def test_deduplicates_same_player_speech_and_decision_public_message(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_1",
                    "message": "先跑一轮看看结果。",
                }
            ],
            "decisions": [
                {
                    "player_id": "player_1",
                    "output": {
                        "public_message": "先跑一轮看看结果。",
                        "private_reason_summary": "普通组队。",
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 1)

    def test_keeps_same_public_message_from_different_decision_players(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "player_id": "player_1",
                    "output": {
                        "public_message": "先跑一轮看看结果。",
                        "private_reason_summary": "普通组队。",
                    },
                },
                {
                    "player_id": "player_2",
                    "output": {
                        "public_message": "先跑一轮看看结果。",
                        "private_reason_summary": "普通组队。",
                    },
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["template_phrase_count"], 2)

    def test_counts_candidate_pressure_as_general_public_action(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_4",
                    "message": "我希望让玩家5上车试试，玩家3你坚持换人，是对玩家2有怀疑吗？",
                }
            ],
            "decisions": [
                {
                    "player_id": "player_4",
                    "output": {
                        "private_reason_summary": "我是派西维尔，候选是玩家1和玩家5，通过让玩家5上车观察反应。",
                        "public_message": "我希望让玩家5上车试试，玩家3你坚持换人，是对玩家2有怀疑吗？",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 1)
        self.assertEqual(summary["candidate_relation_pressure_count"], 0)
        self.assertEqual(summary["candidate_public_action_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 1)

    def test_ignores_private_role_mentions_without_candidate_relationship(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是梅林，知道玩家3和玩家5是坏人，首轮隐藏视角。",
                        "public_message": "玩家2之前没问题，这车失败同车解释。",
                    }
                },
                {
                    "output": {
                        "private_reason_summary": "作为莫甘娜，我想争取上车并支持同伴。",
                        "public_message": "我也想上车吃压力。",
                    }
                },
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，本轮先隐藏视角。",
                        "public_message": "这轮我先听队长解释。",
                    }
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["private_candidate_decision_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_counts_more_table_style_identity_actions(self):
        sample = {
            "speeches": [
                {"player_id": "player_1", "message": "我偏保玩家5这轮上车。"},
                {"player_id": "player_2", "message": "5号这轮先别被打死。"},
                {"player_id": "player_3", "message": "玩家3一直保玩家5，理由给一下。"},
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 3)

    def test_detects_more_public_candidate_binding_risks(self):
        sample = {
            "speeches": [
                {"player_id": "player_1", "message": "3/6两个候选先别打明。"},
                {"player_id": "player_2", "message": "玩家3、玩家6像双候选。"},
                {"player_id": "player_3", "message": "我看到的两个候选是玩家3和玩家6。"},
            ],
            "decisions": [],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["public_candidate_binding_count"], 3)

    def test_counts_private_candidate_to_public_action_conversion(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选。",
                        "public_message": "玩家2一直替玩家5卸压，这关系不自然。",
                    }
                },
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选。",
                        "public_message": "这车我暂时可认。",
                    }
                },
                {
                    "output": {
                        "private_reason_summary": "普通车队判断。",
                        "public_message": "玩家4急着排玩家3，理由是什么？",
                    }
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["private_candidate_decision_count"], 2)
        self.assertEqual(summary["candidate_public_action_count"], 1)
        self.assertEqual(summary["candidate_public_action_gap_count"], 1)

    def test_ignores_private_candidate_reason_on_decisions_without_public_message(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "decision_type": "vote",
                    "output": {
                        "private_reason_summary": "作为派西维尔，我投反对是为了观察两位候选后续反应。",
                    },
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["private_candidate_decision_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_plain_attention_to_candidate_is_not_public_identity_action(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，候选是玩家1和玩家5，需要观察玩家5反应。",
                        "public_message": "我暂时比较关注玩家5的后续发言，看他怎么接这个局势。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["candidate_public_action_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 1)

    def test_ignores_generic_reaction_observation_without_candidate_terms(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是忠臣，想观察玩家3和玩家5的反应。",
                        "public_message": "玩家3和玩家5都先说说态度。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["private_candidate_decision_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_ignores_plain_absence_of_identity_line(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是忠臣，目前无身份线，保持普通好人姿态。",
                        "public_message": "这轮我先问队长为什么跳过玩家4。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["private_candidate_decision_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_counts_general_seat_pressure_separate_from_candidate_relation_pressure(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选，先保护玩家1这车。",
                        "public_message": "玩家3反对想换自己上，我想问玩家3这么急着上车是什么原因？",
                    }
                },
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，候选1和5，这车含候选1排除了候选5。",
                        "public_message": "我同意这车，如果失败会追问玩家3为什么坚持换人。",
                    }
                },
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 2)
        self.assertEqual(summary["candidate_relation_pressure_count"], 0)
        self.assertEqual(summary["candidate_public_action_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 2)

    def test_distinguishes_general_pressure_from_candidate_relation_pressure(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_4",
                    "message": "玩家3这么急着上车是什么原因？",
                },
                {
                    "player_id": "player_4",
                    "message": "玩家5一直把玩家3往车上推，5号先说清楚，为什么觉得3比4更该上？",
                },
            ],
            "decisions": [
                {
                    "player_id": "player_4",
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选，玩家5在推玩家3。",
                        "public_message": "玩家5一直把玩家3往车上推，5号先说清楚，为什么觉得3比4更该上？",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 2)
        self.assertEqual(summary["candidate_relation_pressure_count"], 1)
        self.assertEqual(summary["candidate_public_action_count"], 1)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_counts_exclusion_questions_as_candidate_relation_pressure(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_4",
                    "message": "玩家1为什么把玩家5留在外面？这个排人理由我想听清楚。",
                },
                {
                    "player_id": "player_4",
                    "message": "玩家5上一轮反对1+2说要露脸，现在为什么跳过玩家1？",
                },
                {
                    "player_id": "player_4",
                    "message": "玩家2为什么把玩家3排在外面，3号哪里比我更差？",
                },
            ],
            "decisions": [
                {
                    "player_id": "player_4",
                    "output": {
                        "private_reason_summary": "我是派西维尔，候选是玩家1和玩家5，需要追问候选排除关系。",
                        "public_message": "玩家1为什么把玩家5留在外面？这个排人理由我想听清楚。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 3)
        self.assertEqual(summary["candidate_relation_pressure_count"], 3)
        self.assertEqual(summary["candidate_public_action_count"], 1)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_counts_number_suffix_player_mentions_as_candidate_relation_pressure(self):
        sample = {
            "speeches": [
                {
                    "player_id": "player_4",
                    "message": "玩家2为什么连续两轮跳过5号？我想听这个排人理由。",
                },
                {
                    "player_id": "player_4",
                    "message": "5号一直把3号往车上推，这个动作先解释清楚。",
                },
                {
                    "player_id": "player_4",
                    "message": "玩家2这轮为什么把玩家1排除在外？",
                },
                {
                    "player_id": "player_4",
                    "message": "玩家1之前把5号排在外面，现在这车你支持还是反对？",
                },
            ],
            "decisions": [
                {
                    "player_id": "player_4",
                    "output": {
                        "private_reason_summary": "我是派西维尔，候选是1号和5号，需要追问谁在排候选。",
                        "public_message": "玩家2为什么连续两轮跳过5号？我想听这个排人理由。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 4)
        self.assertEqual(summary["candidate_relation_pressure_count"], 4)
        self.assertEqual(summary["candidate_public_action_count"], 1)
        self.assertEqual(summary["candidate_public_action_gap_count"], 0)

    def test_private_candidate_requires_candidate_relation_pressure(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选，先保护玩家1这车。",
                        "public_message": "玩家3反对想换自己上，我想问玩家3这么急着上车是什么原因？",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            summary = evaluate_prompt_log(path)

        self.assertEqual(summary["identity_contest_action_count"], 1)
        self.assertEqual(summary["candidate_relation_pressure_count"], 0)
        self.assertEqual(summary["candidate_public_action_count"], 0)
        self.assertEqual(summary["candidate_public_action_gap_count"], 1)

    def test_quality_gate_passes_when_identity_contest_is_public_and_clean(self):
        summary = {
            "decision_count": 16,
            "identity_contest_action_count": 2,
            "candidate_relation_pressure_count": 1,
            "private_candidate_decision_count": 2,
            "candidate_public_action_gap_count": 0,
            "public_candidate_binding_count": 0,
            "template_phrase_count": 0,
        }

        gate = evaluate_quality_gate(summary)

        self.assertTrue(gate["passed"])
        self.assertEqual(gate["failures"], [])

    def test_quality_gate_reports_identity_contest_gaps_and_templates(self):
        summary = {
            "decision_count": 6,
            "identity_contest_action_count": 0,
            "candidate_relation_pressure_count": 0,
            "private_candidate_decision_count": 1,
            "candidate_public_action_gap_count": 1,
            "public_candidate_binding_count": 1,
            "template_phrase_count": 2,
        }

        gate = evaluate_quality_gate(summary)

        self.assertFalse(gate["passed"])
        self.assertEqual(
            gate["failures"],
            [
                "sample_too_short",
                "missing_public_identity_contest_action",
                "missing_candidate_relation_pressure",
                "private_candidate_without_public_action",
                "public_candidate_binding_risk",
                "template_phrase_present",
            ],
        )

    def test_cli_can_fail_on_quality_gate(self):
        sample = {
            "speeches": [],
            "decisions": [
                {
                    "output": {
                        "private_reason_summary": "我是派西维尔，玩家1和玩家5是候选。",
                        "public_message": "这车我暂时可认。",
                    }
                }
            ],
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.json"
            path.write_text(json.dumps(sample, ensure_ascii=False), encoding="utf-8")

            result = subprocess.run(
                [
                    sys.executable,
                    "scripts/prompt_log_evaluator.py",
                    str(path),
                    "--fail-on-gate",
                ],
                cwd=Path(__file__).resolve().parents[2],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn('"passed": false', result.stdout)


if __name__ == "__main__":
    unittest.main()
