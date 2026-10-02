import unittest
from hashlib import sha256
from uuid import UUID
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.leagues import live_winner_predictions, save_live_winner_prediction as save_prediction
from app.database import Base
from app.models import LiveMatchWinnerPrediction, Match
from app.schemas import LiveWinnerPredictionRequest


def save_live_winner_prediction(league_id, body, db):
    return save_prediction(league_id, body, db, visitor_hash=sha256(str(body.visitor_id).encode()).hexdigest())


class LiveWinnerPredictionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self.session.add(Match(league_id="season", match_id="match-1", camp1_team_id="team-a",
                               camp2_team_id="team-b", bo=5, start_time="2099-01-01 00:00:00"))
        self.session.commit()
        live = patch("app.api.leagues.live_match_service.get_match_state", return_value={"is_live": True, "current_game": 2})
        live.start()
        self.addCleanup(live.stop)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def request(self, visitor_id: str, winner_team_id: str) -> LiveWinnerPredictionRequest:
        return LiveWinnerPredictionRequest(
            visitor_id=UUID(visitor_id),
            match_id="match-1",
            game_number=2,
            team_a_id="team-a",
            team_b_id="team-b",
            winner_team_id=winner_team_id,
        )

    def test_predictions_are_public_and_each_visitor_has_one_final_vote(self) -> None:
        first = self.request("00000000-0000-4000-8000-000000000001", "team-a")
        second = self.request("00000000-0000-4000-8000-000000000002", "team-b")

        save_live_winner_prediction("season", first, self.session)
        save_live_winner_prediction("season", second, self.session)
        totals = live_winner_predictions("season", "match-1", 2, self.session).data
        self.assertEqual(totals["total_votes"], 2)
        self.assertEqual(totals["votes_by_team"], {"team-a": 1, "team-b": 1})

        changed = self.request("00000000-0000-4000-8000-000000000001", "team-b")
        totals = save_live_winner_prediction("season", changed, self.session).data
        self.assertEqual(totals["total_votes"], 2)
        self.assertEqual(totals["votes_by_team"], {"team-a": 1, "team-b": 1})

    def test_rejects_a_winner_not_in_the_followed_match(self) -> None:
        with self.assertRaises(ValueError):
            self.request("00000000-0000-4000-8000-000000000001", "unrelated-team")

    def test_accepts_zero_for_a_pre_match_series_prediction(self) -> None:
        prediction = LiveWinnerPredictionRequest(
            visitor_id=UUID("00000000-0000-4000-8000-000000000003"),
            match_id="match-1",
            game_number=0,
            team_a_id="team-a",
            team_b_id="team-b",
            winner_team_id="team-a",
            best_of=5,
            team_a_score=3,
            team_b_score=2,
        )
        totals = save_live_winner_prediction("season", prediction, self.session).data
        self.assertEqual(totals["game_number"], 0)
        self.assertEqual(totals["your_best_of"], 5)
        self.assertEqual(totals["your_team_a_score"], 3)
        self.assertEqual(totals["your_team_b_score"], 2)

    def test_rejects_invalid_or_missing_series_scores(self) -> None:
        common = {
            "visitor_id": UUID("00000000-0000-4000-8000-000000000004"),
            "match_id": "match-1",
            "game_number": 0,
            "team_a_id": "team-a",
            "team_b_id": "team-b",
            "winner_team_id": "team-a",
        }
        with self.assertRaises(ValueError):
            LiveWinnerPredictionRequest(**common)
        with self.assertRaises(ValueError):
            LiveWinnerPredictionRequest(
                **common,
                best_of=5,
                team_a_score=2,
                team_b_score=1,
            )

    def test_allows_a_legacy_winner_pick_to_add_its_score_once(self) -> None:
        self.session.query(Match).update({Match.bo: 3})
        self.session.commit()
        visitor_id = "00000000-0000-4000-8000-000000000005"
        self.session.add(
            LiveMatchWinnerPrediction(
                league_id="season",
                match_id="match-1",
                game_number=0,
                visitor_hash=sha256(visitor_id.encode()).hexdigest(),
                winner_team_id="team-a",
            )
        )
        self.session.commit()
        prediction = LiveWinnerPredictionRequest(
            visitor_id=UUID(visitor_id),
            match_id="match-1",
            game_number=0,
            team_a_id="team-a",
            team_b_id="team-b",
            winner_team_id="team-a",
            best_of=3,
            team_a_score=2,
            team_b_score=1,
        )
        totals = save_live_winner_prediction("season", prediction, self.session).data
        self.assertEqual(totals["your_team_a_score"], 2)
        self.assertEqual(totals["your_team_b_score"], 1)


if __name__ == "__main__":
    unittest.main()
