import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.services import draft_commentary


def hero(
    hero_id: int,
    name: str,
    *,
    mechanics: list[str],
    conditions: list[str] | None = None,
) -> dict:
    return {
        "hero_id": hero_id,
        "hero_name": name,
        "mechanics": mechanics,
        "conditions": conditions or [],
    }


def with_roles(profile: dict, *roles: tuple[str, str]) -> dict:
    return {
        **profile,
        "tactical": {
            "tactical_roles": [
                {"key": key, "label_zh": label, "confidence": "high"}
                for key, label in roles
            ],
            "official_classes": [],
            "official_relationships": {},
        },
    }


class DraftCommentaryEvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.zhangfei = hero(
            171,
            "张飞",
            mechanics=["control_stun", "control_knockback", "damage_physical"],
        )
        self.shenmengxi = hero(
            312,
            "沈梦溪",
            mechanics=["control_knockup", "damage_magic"],
            conditions=["channel_or_charge", "directional"],
        )

    def test_control_precision_synergy_keeps_correct_direction_for_either_pick_order(self) -> None:
        zhangfei_selected = draft_commentary._interaction_claims(
            self.zhangfei, [self.shenmengxi], []
        )
        shenmengxi_selected = draft_commentary._interaction_claims(
            self.shenmengxi, [self.zhangfei], []
        )

        expected = "张飞的硬控能为沈梦溪的蓄力、延迟或方向性技能创造更稳定的命中窗口"
        self.assertIn(expected, [claim["detail"] for claim in zhangfei_selected])
        self.assertIn(expected, [claim["detail"] for claim in shenmengxi_selected])
        self.assertNotIn(
            "沈梦溪的硬控能为张飞的蓄力、延迟或方向性技能创造更稳定的命中窗口",
            [claim["detail"] for claim in shenmengxi_selected],
        )
        self.assertNotIn(
            "control_chain",
            [claim.get("rule") for claim in shenmengxi_selected],
        )

    def test_plain_control_plus_plain_damage_is_not_called_synergy(self) -> None:
        controller = hero(1, "控制英雄", mechanics=["control_stun"])
        damage = hero(2, "输出英雄", mechanics=["damage_magic"])

        claims = draft_commentary._interaction_claims(controller, [damage], [])

        self.assertEqual(claims, [])

    def test_tactical_roles_turn_zhangfei_shenmengxi_into_protect_poke(self) -> None:
        zhangfei = with_roles(
            self.zhangfei,
            ("peel_disengage", "拆火/劝退"),
            ("counter_engage", "反打"),
            ("frontline", "前排承伤"),
        )
        shenmengxi = with_roles(
            self.shenmengxi,
            ("long_range_poke", "远程消耗"),
            ("zone_damage", "区域压制"),
        )

        claim = draft_commentary._tactical_pair_claim(zhangfei, shenmengxi)
        reversed_claim = draft_commentary._tactical_pair_claim(shenmengxi, zhangfei)

        self.assertEqual(claim["rule"], "protect_poke_structure")
        self.assertEqual(claim["detail"], reversed_claim["detail"])
        self.assertIn("张飞负责拆火/劝退、反打", claim["detail"])
        self.assertIn("沈梦溪可以保持距离承担远程消耗、区域压制", claim["detail"])
        self.assertNotIn("创造更稳定的命中窗口", claim["detail"])

    def test_reposition_is_connected_to_carry_job_not_generic_damage(self) -> None:
        master = with_roles(
            hero(525, "鲁班大师", mechanics=["support_ally_reposition", "control_pull"]),
            ("ally_reposition", "队友位置调整"),
            ("primary_engage", "主动开团"),
        )
        carry = with_roles(
            hero(112, "鲁班七号", mechanics=["damage_physical"]),
            ("ranged_carry", "远程核心输出"),
        )

        claim = draft_commentary._tactical_pair_claim(master, carry)

        self.assertEqual(claim["rule"], "reposition_carry_structure")
        self.assertIn("队友位置调整", claim["detail"])
        self.assertIn("更安全的远程核心输出空间", claim["detail"])

    def test_tactical_identity_does_not_attach_unrelated_mobility(self) -> None:
        poke = with_roles(
            hero(
                312,
                "沈梦溪",
                mechanics=["damage_magic", "mobility_dash", "utility_vision"],
            ),
            ("long_range_poke", "远程消耗"),
            ("vision_control", "视野控制"),
        )

        claim = draft_commentary._tactical_identity_claim(poke)

        self.assertIn("远程消耗、视野控制", claim["detail"])
        self.assertIn("视野获取", claim["detail"])
        self.assertNotIn("突进/位移", claim["detail"])

    def test_historical_response_requires_supported_above_baseline_lift(self) -> None:
        enemy = hero(10, "敌方英雄", mechanics=["damage_magic"])
        supported = {
            "relation": "counter_pick",
            "context_level": "overall",
            "is_peak_battle": False,
            "opponent_hero_id": 10,
            "opponent_hero_name": "敌方英雄",
            "candidate_hero_id": 312,
            "selection_count": 6,
            "legal_opportunity_count": 92,
            "smoothed_lift": 1.6,
        }

        claim = draft_commentary._historical_counter_claim(
            (supported,), selected=self.shenmengxi, enemies=[enemy]
        )
        below_baseline = draft_commentary._historical_counter_claim(
            ({**supported, "smoothed_lift": 0.86},),
            selected=self.shenmengxi,
            enemies=[enemy],
        )

        self.assertIsNotNone(claim)
        self.assertEqual(claim["kind"], "历史应对")
        self.assertIsNone(below_baseline)

    def test_kimi_must_use_mechanism_and_team_usage_when_both_exist(self) -> None:
        evidence = [
            {"id": "claim_1", "kind": "阵容联动"},
            {"id": "claim_2", "kind": "战队联动"},
            {"id": "claim_3", "kind": "技能机制"},
        ]

        self.assertEqual(
            draft_commentary._required_claim_ids(evidence),
            ["claim_1", "claim_2"],
        )

    def test_visible_evidence_references_are_rejected(self) -> None:
        for text in (
            "根据claim1，这套阵容更偏消耗。",
            "结合 claim_2 和 claim 3，可以看出阵容分工。",
            "依据编号2，这一手负责拆火。",
        ):
            with self.subTest(text=text):
                self.assertTrue(
                    draft_commentary._contains_visible_evidence_reference(text)
                )
        self.assertFalse(
            draft_commentary._contains_visible_evidence_reference(
                "张飞负责拆火，沈梦溪保持距离消耗。"
            )
        )

    def test_kimi_output_with_visible_claim_id_falls_back(self) -> None:
        settings = MagicMock()
        settings.kimi_timeout_seconds = 5.0
        settings.kimi_model = "test-model"
        settings.model_copy.return_value = settings
        client = MagicMock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=(
                '{"commentary":"根据claim1，张飞负责拆火。",'
                '"used_evidence_ids":["claim_1"]}'
            )))]
        )
        brief = {
            "event": {"hero": "张飞"},
            "claims": [{"id": "claim_1", "detail": "张飞负责拆火"}],
            "instructions": {"required_evidence_ids": ["claim_1"]},
        }

        with (
            patch.object(draft_commentary, "get_settings", return_value=settings),
            patch.object(draft_commentary, "build_kimi_client", return_value=client),
        ):
            result = draft_commentary._generate_llm_commentary(brief)

        self.assertIsNone(result)

    def test_unsupported_skill_detail_or_intent_is_rejected(self) -> None:
        self.assertTrue(
            draft_commentary._contains_unsupported_inference(
                "张飞控制能帮助沈梦溪的大招命中，是战队主动追求的组合。"
            )
        )
        self.assertTrue(
            draft_commentary._contains_unsupported_inference(
                "这是KSG战术库中的高频组合。"
            )
        )
        self.assertFalse(
            draft_commentary._contains_unsupported_inference(
                "张飞硬控为沈梦溪的蓄力和方向性技能创造命中窗口。"
            )
        )


if __name__ == "__main__":
    unittest.main()
