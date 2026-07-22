"""BP data QA for a single league/season.

Usage examples (from repo root):

  python3 analysis/qa_bp.py --year 2026 --name 挑战者杯
  python3 analysis/qa_bp.py --league-id 20260002
  python3 analysis/qa_bp.py --year 2025 --name 挑战者杯
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from common import DB_PATH, connect, resolve_league

# Typical KPL global-BP game: 10 bans + 10 picks.
EXPECTED_BP_ACTIONS = 20
EXPECTED_BANS = 10
EXPECTED_PICKS = 10


@dataclass
class MatchIssue:
    match_id: str
    detail: str
    camp1: str = ""
    camp2: str = ""
    score: str = ""
    bo: int = 0
    n_battles: int = 0


@dataclass
class BattleIssue:
    match_id: str
    battle_id: str
    battle_seq: int
    detail: str
    bans: int = 0
    picks: int = 0
    total: int = 0


@dataclass
class HeroReuse:
    match_id: str
    team_id: str
    team_name: str
    hero_id: int
    hero_name: str
    battle_seqs: list[int]
    pick_count: int


@dataclass
class QaReport:
    league_id: str
    league_name: str
    year: int | None
    season: int | None
    match_count: int = 0
    battle_count: int = 0
    bp_count: int = 0
    matches_missing_battles: list[MatchIssue] = field(default_factory=list)
    matches_score_mismatch: list[MatchIssue] = field(default_factory=list)
    matches_battle_gap: list[MatchIssue] = field(default_factory=list)
    battles_incomplete_bp: list[BattleIssue] = field(default_factory=list)
    battles_peak_candidates: list[BattleIssue] = field(default_factory=list)
    battles_missing_win_camp: list[BattleIssue] = field(default_factory=list)
    battles_missing_players: list[BattleIssue] = field(default_factory=list)
    hero_pick_reuses: list[HeroReuse] = field(default_factory=list)
    unmapped_pick_count: int = 0
    unknown_hero_ids: list[int] = field(default_factory=list)
    bp_missing_hero: int = 0


def _score_label(camp1_score: int, camp2_score: int) -> str:
    return f"{camp1_score}-{camp2_score}"


def check_match_completeness(conn, league_id: str) -> tuple[list[MatchIssue], list[MatchIssue], list[MatchIssue]]:
    """Flag matches with missing battles, score≠battle count, or non-contiguous battle_seq."""
    missing: list[MatchIssue] = []
    score_mismatch: list[MatchIssue] = []
    battle_gaps: list[MatchIssue] = []

    rows = conn.execute(
        """
        SELECT
            m.match_id,
            m.camp1_team_name,
            m.camp2_team_name,
            m.camp1_score,
            m.camp2_score,
            m.bo,
            COUNT(b.battle_id) AS n_battles,
            GROUP_CONCAT(b.battle_seq) AS seqs
        FROM matches m
        LEFT JOIN battles b ON b.match_id = m.match_id
        WHERE m.league_id = ?
        GROUP BY m.match_id
        ORDER BY m.match_id
        """,
        (league_id,),
    ).fetchall()

    for row in rows:
        expected_games = int(row["camp1_score"] or 0) + int(row["camp2_score"] or 0)
        n_battles = int(row["n_battles"] or 0)
        base = MatchIssue(
            match_id=row["match_id"],
            detail="",
            camp1=row["camp1_team_name"] or "",
            camp2=row["camp2_team_name"] or "",
            score=_score_label(row["camp1_score"] or 0, row["camp2_score"] or 0),
            bo=int(row["bo"] or 0),
            n_battles=n_battles,
        )

        if n_battles == 0:
            issue = MatchIssue(**{**asdict(base), "detail": "no battles synced"})
            missing.append(issue)
            continue

        if expected_games > 0 and n_battles != expected_games:
            issue = MatchIssue(
                **{
                    **asdict(base),
                    "detail": f"battle count {n_battles} != score sum {expected_games}",
                }
            )
            score_mismatch.append(issue)

        seqs = sorted(int(s) for s in (row["seqs"] or "").split(",") if s)
        if seqs:
            expected_seq = list(range(1, max(seqs) + 1))
            if seqs != expected_seq:
                issue = MatchIssue(
                    **{
                        **asdict(base),
                        "detail": f"battle_seq not contiguous: {seqs}",
                    }
                )
                battle_gaps.append(issue)

    return missing, score_mismatch, battle_gaps


def check_battle_bp(conn, league_id: str) -> tuple[list[BattleIssue], list[BattleIssue], list[BattleIssue]]:
    """Flag incomplete BP rows, peak candidates (0 bans), and missing win_camp."""
    incomplete: list[BattleIssue] = []
    peak: list[BattleIssue] = []
    missing_win: list[BattleIssue] = []

    rows = conn.execute(
        """
        SELECT
            b.match_id,
            b.battle_id,
            b.battle_seq,
            b.win_camp,
            COALESCE(SUM(CASE WHEN bp.action_type = 0 THEN 1 ELSE 0 END), 0) AS bans,
            COALESCE(SUM(CASE WHEN bp.action_type = 1 THEN 1 ELSE 0 END), 0) AS picks,
            COUNT(bp.id) AS total
        FROM battles b
        LEFT JOIN battle_bps bp ON bp.battle_id = b.battle_id
        WHERE b.league_id = ?
        GROUP BY b.battle_id
        ORDER BY b.match_id, b.battle_seq
        """,
        (league_id,),
    ).fetchall()

    for row in rows:
        bans = int(row["bans"])
        picks = int(row["picks"])
        total = int(row["total"])
        issue = BattleIssue(
            match_id=row["match_id"],
            battle_id=row["battle_id"],
            battle_seq=int(row["battle_seq"] or 0),
            detail="",
            bans=bans,
            picks=picks,
            total=total,
        )

        if int(row["win_camp"] or 0) == 0:
            missing_win.append(
                BattleIssue(**{**asdict(issue), "detail": "win_camp missing/0"})
            )

        if bans == 0 and picks > 0:
            peak.append(
                BattleIssue(
                    **{
                        **asdict(issue),
                        "detail": f"0 bans / {picks} picks (peak candidate)",
                    }
                )
            )

        # Standard game expectation; peak candidates are reported separately.
        if bans > 0 and (
            total != EXPECTED_BP_ACTIONS
            or bans != EXPECTED_BANS
            or picks != EXPECTED_PICKS
        ):
            incomplete.append(
                BattleIssue(
                    **{
                        **asdict(issue),
                        "detail": (
                            f"expected {EXPECTED_BANS}b/{EXPECTED_PICKS}p "
                            f"({EXPECTED_BP_ACTIONS} total), got {bans}b/{picks}p ({total})"
                        ),
                    }
                )
            )
        elif bans == 0 and picks not in (0, EXPECTED_PICKS):
            # Peak-shaped but not a full 10-pick draft.
            incomplete.append(
                BattleIssue(
                    **{
                        **asdict(issue),
                        "detail": f"peak-like draft with {picks} picks (expected {EXPECTED_PICKS} or empty)",
                    }
                )
            )
        elif total == 0:
            incomplete.append(
                BattleIssue(**{**asdict(issue), "detail": "no BP rows"})
            )

    return incomplete, peak, missing_win


def _has_table(conn, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def check_battles_missing_players(conn, league_id: str) -> list[BattleIssue]:
    """Battles that have no battle_players rows (can't map camp → team)."""
    if not _has_table(conn, "battle_players"):
        return [
            BattleIssue(
                match_id="",
                battle_id="",
                battle_seq=0,
                detail="battle_players table missing — run analysis/sync_battle_players.py",
            )
        ]

    rows = conn.execute(
        """
        SELECT b.match_id, b.battle_id, b.battle_seq
        FROM battles b
        LEFT JOIN battle_players pl ON pl.battle_id = b.battle_id
        WHERE b.league_id = ?
          AND pl.id IS NULL
        ORDER BY b.match_id, b.battle_seq
        """,
        (league_id,),
    ).fetchall()
    return [
        BattleIssue(
            match_id=r["match_id"],
            battle_id=r["battle_id"],
            battle_seq=int(r["battle_seq"] or 0),
            detail="no battle_players rows",
        )
        for r in rows
    ]


def check_hero_pick_reuse(conn, league_id: str) -> tuple[list[HeroReuse], int]:
    """Same team picking the same hero in 2+ battles of one match.

    Maps BP picks → team via battle_players using API camp + hero_id
    (battle_bps.camp and battle_players.camp are both API-side).
    Opposite teams may each pick the same hero once; that is allowed.
    """
    if not _has_table(conn, "battle_players"):
        return [], 0

    hero_name_expr = (
        "COALESCE(h.hero_name, MAX(bp.hero_name), '')"
        if _has_table(conn, "heroes")
        else "MAX(bp.hero_name)"
    )
    heroes_join = (
        "LEFT JOIN heroes h ON h.hero_id = bp.hero_id"
        if _has_table(conn, "heroes")
        else ""
    )

    unmapped = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM battles b
        JOIN battle_bps bp
          ON bp.battle_id = b.battle_id
         AND bp.action_type = 1
         AND bp.hero_id > 0
        LEFT JOIN battle_players pl
          ON pl.battle_id = bp.battle_id
         AND pl.camp = bp.camp
         AND pl.hero_id = bp.hero_id
        WHERE b.league_id = ?
          AND pl.id IS NULL
        """,
        (league_id,),
    ).fetchone()["n"]

    rows = conn.execute(
        f"""
        SELECT
            b.match_id,
            pl.team_id,
            MAX(pl.team_name) AS team_name,
            bp.hero_id,
            {hero_name_expr} AS hero_name,
            GROUP_CONCAT(b.battle_seq) AS seqs,
            COUNT(*) AS pick_count
        FROM battles b
        JOIN battle_bps bp
          ON bp.battle_id = b.battle_id
         AND bp.action_type = 1
         AND bp.hero_id > 0
        JOIN battle_players pl
          ON pl.battle_id = bp.battle_id
         AND pl.camp = bp.camp
         AND pl.hero_id = bp.hero_id
        {heroes_join}
        WHERE b.league_id = ?
        GROUP BY b.match_id, pl.team_id, bp.hero_id
        HAVING COUNT(*) > 1
        ORDER BY b.match_id, pick_count DESC, pl.team_id, bp.hero_id
        """,
        (league_id,),
    ).fetchall()

    out: list[HeroReuse] = []
    for row in rows:
        seqs = sorted(int(s) for s in (row["seqs"] or "").split(",") if s)
        out.append(
            HeroReuse(
                match_id=row["match_id"],
                team_id=row["team_id"] or "",
                team_name=row["team_name"] or "",
                hero_id=int(row["hero_id"]),
                hero_name=row["hero_name"] or "",
                battle_seqs=seqs,
                pick_count=int(row["pick_count"]),
            )
        )
    return out, int(unmapped)


def check_hero_refs(conn, league_id: str) -> tuple[list[int], int]:
    """Unknown hero_ids (vs heroes table) and BP rows with hero_id<=0."""
    missing_hero = conn.execute(
        """
        SELECT COUNT(*) AS n
        FROM battle_bps
        WHERE league_id = ? AND hero_id <= 0
        """,
        (league_id,),
    ).fetchone()["n"]

    if not _has_table(conn, "heroes"):
        return [], int(missing_hero)

    rows = conn.execute(
        """
        SELECT DISTINCT bp.hero_id
        FROM battle_bps bp
        LEFT JOIN heroes h ON h.hero_id = bp.hero_id
        WHERE bp.league_id = ?
          AND bp.hero_id > 0
          AND h.hero_id IS NULL
        ORDER BY bp.hero_id
        """,
        (league_id,),
    ).fetchall()
    return [int(r["hero_id"]) for r in rows], int(missing_hero)


def run_qa(conn, league_id: str, league_meta: Any | None = None) -> QaReport:
    if league_meta is None:
        league_meta = conn.execute(
            "SELECT * FROM leagues WHERE league_id = ?",
            (league_id,),
        ).fetchone()
        if not league_meta:
            raise ValueError(f"No league with league_id={league_id!r}")

    counts = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM matches WHERE league_id = ?) AS match_count,
            (SELECT COUNT(*) FROM battles WHERE league_id = ?) AS battle_count,
            (SELECT COUNT(*) FROM battle_bps WHERE league_id = ?) AS bp_count
        """,
        (league_id, league_id, league_id),
    ).fetchone()

    missing, score_mismatch, gaps = check_match_completeness(conn, league_id)
    incomplete, peak, missing_win = check_battle_bp(conn, league_id)
    missing_players = check_battles_missing_players(conn, league_id)
    reuses, unmapped_picks = check_hero_pick_reuse(conn, league_id)
    unknown_ids, bp_missing = check_hero_refs(conn, league_id)

    return QaReport(
        league_id=league_id,
        league_name=league_meta["league_name"] or "",
        year=league_meta["year"],
        season=league_meta["season"],
        match_count=int(counts["match_count"]),
        battle_count=int(counts["battle_count"]),
        bp_count=int(counts["bp_count"]),
        matches_missing_battles=missing,
        matches_score_mismatch=score_mismatch,
        matches_battle_gap=gaps,
        battles_incomplete_bp=incomplete,
        battles_peak_candidates=peak,
        battles_missing_win_camp=missing_win,
        battles_missing_players=missing_players,
        hero_pick_reuses=reuses,
        unmapped_pick_count=unmapped_picks,
        unknown_hero_ids=unknown_ids,
        bp_missing_hero=bp_missing,
    )


def _print_section(title: str, items: list[Any], limit: int, formatter) -> None:
    print(f"\n## {title} ({len(items)})")
    if not items:
        print("  (none)")
        return
    for item in items[:limit]:
        print(f"  - {formatter(item)}")
    if len(items) > limit:
        print(f"  ... and {len(items) - limit} more")


def print_report(report: QaReport, *, sample_limit: int = 15) -> None:
    print(f"# BP QA — {report.league_name} ({report.league_id})")
    print(f"year={report.year} season={report.season}")
    print(
        f"counts: matches={report.match_count} "
        f"battles={report.battle_count} bp_rows={report.bp_count}"
    )

    _print_section(
        "Matches missing battles",
        report.matches_missing_battles,
        sample_limit,
        lambda m: f"{m.match_id} {m.camp1} vs {m.camp2} ({m.score}) — {m.detail}",
    )
    _print_section(
        "Matches score vs battle count mismatch",
        report.matches_score_mismatch,
        sample_limit,
        lambda m: f"{m.match_id} {m.camp1} vs {m.camp2} ({m.score}) battles={m.n_battles} — {m.detail}",
    )
    _print_section(
        "Matches with battle_seq gaps",
        report.matches_battle_gap,
        sample_limit,
        lambda m: f"{m.match_id} — {m.detail}",
    )
    _print_section(
        "Battles with incomplete / odd BP",
        report.battles_incomplete_bp,
        sample_limit,
        lambda b: f"{b.match_id} G{b.battle_seq} {b.battle_id} — {b.detail}",
    )
    _print_section(
        "Peak candidates (0 bans)",
        report.battles_peak_candidates,
        sample_limit,
        lambda b: f"{b.match_id} G{b.battle_seq} bans={b.bans} picks={b.picks} — {b.detail}",
    )
    _print_section(
        "Battles missing win_camp",
        report.battles_missing_win_camp,
        sample_limit,
        lambda b: f"{b.match_id} G{b.battle_seq} {b.battle_id}",
    )
    _print_section(
        "Battles missing player rows",
        report.battles_missing_players,
        sample_limit,
        lambda b: (
            b.detail
            if not b.battle_id
            else f"{b.match_id} G{b.battle_seq} {b.battle_id} — {b.detail}"
        ),
    )
    _print_section(
        "Same-team hero pick reuse within match",
        report.hero_pick_reuses,
        sample_limit,
        lambda r: (
            f"{r.match_id} {r.team_name}({r.team_id}) {r.hero_name}({r.hero_id}) "
            f"x{r.pick_count} in games {r.battle_seqs}"
        ),
    )
    print(f"\n## Pick → team mapping")
    print(f"  BP picks with no matching battle_player: {report.unmapped_pick_count}")

    print(f"\n## Hero reference issues")
    print(f"  BP rows with hero_id<=0: {report.bp_missing_hero}")
    print(f"  Unknown hero_ids (not in heroes table): {len(report.unknown_hero_ids)}")
    if report.unknown_hero_ids:
        shown = report.unknown_hero_ids[:sample_limit]
        extra = len(report.unknown_hero_ids) - len(shown)
        suffix = f" ... +{extra} more" if extra > 0 else ""
        print(f"  ids: {shown}{suffix}")


def report_to_dict(report: QaReport) -> dict[str, Any]:
    return asdict(report)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="QA BP data for one KPL league/season")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to kpl_bp.db")
    parser.add_argument("--league-id", default=None, help="Exact league_id, e.g. 20260002")
    parser.add_argument("--year", type=int, default=None, help="League year filter")
    parser.add_argument(
        "--name",
        dest="name_contains",
        default=None,
        help="Substring match on league_name, e.g. 挑战者杯",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Optional path to write full JSON report",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=15,
        help="Max rows printed per section",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if not args.league_id and args.year is None and not args.name_contains:
        # Sensible default for the current analysis target; override via flags.
        args.year = 2026
        args.name_contains = "挑战者杯"

    with connect(args.db) as conn:
        league = resolve_league(
            conn,
            league_id=args.league_id,
            year=args.year,
            name_contains=args.name_contains,
        )
        report = run_qa(conn, league["league_id"], league)

    print_report(report, sample_limit=args.sample_limit)

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(
            json.dumps(report_to_dict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nWrote JSON report to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
