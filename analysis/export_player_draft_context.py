#!/usr/bin/env python3
"""Export a pinned as-of context snapshot for the simple personalization runtime."""

from __future__ import annotations

import argparse,hashlib,json
from datetime import date
from pathlib import Path
from tempfile import NamedTemporaryFile

from player_context.history import TemporalContextBuilder,load_historical_games


def main()->None:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--source-seasons",required=True);p.add_argument("--hero-ids",type=Path,required=True,help="Personalized or sequence artifact");p.add_argument("--context-as-of",required=True);p.add_argument("--output",type=Path,required=True);a=p.parse_args()
    root=Path(__file__).resolve().parents[1];seasons=[v.strip() for v in a.source_seasons.split(",") if v.strip()];artifact=json.loads(a.hero_ids.read_text());hero_ids=[int(v) for v in artifact["hero_ids"]]
    games=load_historical_games([root/"analysis"/"exports"/s/"matches.jsonl" for s in seasons]);builder=TemporalContextBuilder(games,hero_ids);cutoff=date.fromisoformat(a.context_as_of)
    team_season=seasons[-1]
    target_games=load_historical_games([root/"analysis"/"exports"/team_season/"matches.jsonl"])
    teams=sorted({appearance.team_id for game in target_games if game.event_date<cutoff for appearance in game.appearances});pairs={}
    for own in teams:
        for opponent in teams:
            if own==opponent:continue
            context=builder.build(own,opponent,cutoff)
            coverage=[]
            for team in range(2):
                coverage.append(sum(sum(c["weight"]*h["reliability"] for c,h in zip(context["rosters"][team][r],context["histories"][team][r])) for r in range(5))/5)
            pairs[f"{own}|{opponent}"]={"familiarity":context["familiarity"],"hero_role_prior":context["hero_role_prior"],"coverage":coverage,"roster_source":context["roster_source"]}
    identity={"schema_version":1,"context_contract_version":"player_context_v1","context_as_of":cutoff.isoformat(),"hero_ids":hero_ids,"source_seasons":seasons,"team_season":team_season,"maximum_source_event_date":max(g.event_date for g in games if g.event_date<cutoff).isoformat(),"pairs":pairs}
    identity["context_id"]=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":")).encode()).hexdigest();a.output.parent.mkdir(parents=True,exist_ok=True)
    with NamedTemporaryFile("w",encoding="utf-8",dir=a.output.parent,delete=False) as tmp:json.dump(identity,tmp,ensure_ascii=False,separators=(",",":"));tmp.write("\n");temporary=Path(tmp.name)
    temporary.replace(a.output);print(f"Wrote {a.output.resolve()} ({len(pairs)} ordered team pairs)")


if __name__=="__main__":main()
