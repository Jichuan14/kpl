import unittest
from uuid import UUID

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.leagues import live_winner_predictions, save_live_winner_prediction
from app.database import Base
from app.schemas import LiveWinnerPredictionRequest


class LiveWinnerPredictionApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

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
        prediction = self.request("00000000-0000-4000-8000-000000000003", "team-a")
        prediction.game_number = 0
        totals = save_live_winner_prediction("season", prediction, self.session).data
        self.assertEqual(totals["game_number"], 0)


if __name__ == "__main__":
    unittest.main()
