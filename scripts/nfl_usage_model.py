"""Weekly NFL component model using only statistics available before its cutoff.

No projection workbook, expert ranking, ADP, news text, or paid provider is an
input. The same feature builder and predictor serve historical replay and the
current release. Model choices are stored separately with their evaluation.
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

VERSION = 'nfl-usage-v2'
POSITIONS = ('QB', 'RB', 'WR', 'TE')
FORMATS = {'ppr': 1.0, 'half_ppr': .5, 'non_ppr': 0.0}
STATS = ('attempts', 'completions', 'passing_yards', 'passing_tds',
         'passing_interceptions', 'carries', 'rushing_yards', 'rushing_tds',
         'targets', 'receptions', 'receiving_yards', 'receiving_tds',
         'fumbles_lost_total', 'passing_2pt_conversions', 'rushing_2pt_conversions',
         'receiving_2pt_conversions', 'special_teams_tds')
CONTEXT = ('goal_line_carries', 'red_zone_targets', 'end_zone_targets',
           'neutral_passes', 'neutral_plays', 'offense_pct')
ALIASES = {'LA':'LAR', 'JAC':'JAX', 'WSH':'WAS', 'OAK':'LV', 'SD':'LAC', 'STL':'LAR'}


def team(value):
    return ALIASES.get(str(value or '').upper(), str(value or '').upper())


def num(row, key):
    value = row.get(key)
    if value in ('', None, 'NA', 'NaN', 'nan'):
        return 0.0
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'Nonfinite {key}')
    return value


def score(s, reception=1.0):
    return (s.get('passing_yards',0)*.04 + s.get('passing_tds',0)*4
            - s.get('passing_interceptions',0)*2 + s.get('rushing_yards',0)*.1
            + s.get('rushing_tds',0)*6 + s.get('receiving_yards',0)*.1
            + s.get('receiving_tds',0)*6 + s.get('receptions',0)*reception
            - s.get('fumbles_lost_total',0)*2
            + sum(s.get(k,0)*2 for k in ('passing_2pt_conversions','rushing_2pt_conversions','receiving_2pt_conversions'))
            + s.get('special_teams_tds',0)*6)


def read_rows(path):
    with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as handle:
        yield from csv.DictReader(handle)


def average(values, default=0.0):
    return statistics.fmean(values) if values else default


def weighted_average(values, default=0.0):
    """A four-appearance half-life gives recent role changes more influence."""
    if not values:
        return default
    weights = [2 ** (-(len(values)-i-1)/4) for i in range(len(values))]
    return sum(v*w for v,w in zip(values,weights))/sum(weights)


def clamp(value, low, high):
    return max(low, min(high, value))


def blend(prior, current, appearances, strength):
    if current is None:
        return prior
    weight = appearances/(appearances+strength)
    return prior*(1-weight) + current*weight


class History:
    def __init__(self, cache, years=(2023,2024,2025,2026)):
        self.cache = Path(cache)
        self.years = years
        self.games = list(read_rows(self.cache/'games.csv.gz'))
        self.player = defaultdict(list)
        self.teams = defaultdict(list)
        self.rosters = {}
        self.depth = {}
        self.actual = {}
        self.context = {}
        self.snap = {}
        self._features = {}
        pfr_candidates = defaultdict(set)
        for year in years:
            roster_path = self.cache/f'roster_weekly_{year}.csv.gz'
            if not roster_path.exists():
                roster_path = self.cache/f'roster_{year}.csv.gz'
            self.rosters[year] = list(read_rows(roster_path))
            for r in self.rosters[year]:
                if r.get('pfr_id') and r.get('gsis_id'):
                    pfr_candidates[r['pfr_id']].add(r['gsis_id'])
            self.depth[year] = self._read_depth(year)
        # An upstream collision for two defensive players must neither join
        # their snaps nor block unrelated offensive identities.
        pfr_ids = {key:next(iter(ids)) for key,ids in pfr_candidates.items() if len(ids)==1}
        self.ambiguous_snap_ids = sorted(key for key,ids in pfr_candidates.items() if len(ids)>1)
        for year in years:
            # Reduce the wide play-by-play source once, without retaining text.
            context_path = self.cache/f'usage_context_{year}.json'
            pbp = self.cache/f'play_by_play_{year}.csv.gz'
            import hashlib
            digest = hashlib.sha256(pbp.read_bytes()).hexdigest()
            if context_path.exists():
                context = json.loads(context_path.read_text())
            else:
                context = {}
            if context.get('source_sha256') != digest:
                context = self._reduce_pbp(pbp)
                context['source_sha256'] = digest
                context_path.write_text(json.dumps(context,sort_keys=True,separators=(',',':'))+'\n')
            self.context.update(context['players'])
            team_context = context['teams']
            for r in read_rows(self.cache/f'stats_team_week_{year}.csv.gz'):
                if r.get('season_type') != 'REG':
                    continue
                club = team(r['team'])
                item = {k:num(r,k) for k in STATS}
                item.update(season=int(r['season']), week=int(r['week']), team=club,
                            opponent=team(r['opponent_team']),game_id=r['game_id'])
                item.update(team_context.get(r['game_id']+'|'+club,{}))
                self.teams[club].append(item)
            # Stats and snaps are independently keyed. No stat row is invented
            # until a recorded offensive appearance establishes participation.
            year_stats = {}
            for r in read_rows(self.cache/f'stats_player_week_{year}.csv.gz'):
                if r.get('season_type') != 'REG' or not r.get('player_id'):
                    continue
                key = (r['player_id'],r['game_id'])
                if key in year_stats:
                    raise ValueError(f'Duplicate player-game {key}')
                item = {k:num(r,k) for k in STATS}
                item.update(season=int(r['season']),week=int(r['week']),team=team(r['team']),
                            opponent=team(r['opponent_team']),game_id=r['game_id'],
                            id=r['player_id'],position=r['position'],name=r['player_display_name'],
                            recorded_stats=True)
                item.update(self.context.get(r['game_id']+'|'+r['player_id'],{}))
                year_stats[key] = item
                self.actual[key] = item
            for r in read_rows(self.cache/f'snap_counts_{year}.csv.gz'):
                if r.get('game_type') != 'REG':
                    continue
                pid = pfr_ids.get(r.get('pfr_player_id'))
                if not pid:
                    continue
                key = (pid,r['game_id'])
                self.snap[key] = {'offense_pct':num(r,'offense_pct'),'offense_snaps':num(r,'offense_snaps')}
                if key not in year_stats and num(r,'offense_snaps') > 0 and r.get('position') in POSITIONS:
                    year_stats[key] = {k:0.0 for k in STATS}
                    year_stats[key].update(season=year,week=int(r['week']),team=team(r['team']),
                        opponent=team(r['opponent']),game_id=r['game_id'],id=pid,
                        position=r['position'],name=r['player'],recorded_stats=False)
                if key in year_stats:
                    year_stats[key].update(self.snap[key])
            for (pid,_),item in year_stats.items():
                self.player[pid].append(item)
        for group in (self.player,self.teams):
            for rows in group.values():
                rows.sort(key=lambda r:(r['season'],r['week'],r['game_id']))
        self.team_games = {(r['game_id'],club):r for club,rows in self.teams.items() for r in rows}

    def _read_depth(self, year):
        grouped = defaultdict(list)
        for r in read_rows(self.cache/f'depth_charts_{year}.csv.gz'):
            if not r.get('gsis_id'):
                continue
            if year >= 2025:
                pos = r.get('pos_abb')
                stamp = r.get('dt')
                club = team(r.get('team'))
                rank = int(num(r,'pos_rank'))
            else:
                if r.get('game_type') != 'REG':
                    continue
                pos = r.get('position')
                stamp = int(r['week'])
                club = team(r.get('club_code'))
                rank = int(num(r,'depth_team'))
            if pos in POSITIONS and rank > 0:
                grouped[stamp].append({'id':r['gsis_id'],'team':club,'position':pos,'rank':rank})
        return dict(grouped)

    @staticmethod
    def _reduce_pbp(path):
        players = defaultdict(lambda:defaultdict(float))
        teams = defaultdict(lambda:defaultdict(float))
        for r in read_rows(path):
            if r.get('season_type') != 'REG' or r.get('play_type') == 'no_play':
                continue
            if num(r,'qb_kneel') or num(r,'qb_spike'):
                continue
            club = team(r.get('posteam'))
            if not club:
                continue
            t = teams[r['game_id']+'|'+club]
            if num(r,'qtr') <= 3 and abs(num(r,'score_differential')) <= 8:
                if num(r,'rush_attempt') or num(r,'pass_attempt') or num(r,'sack'):
                    t['neutral_plays'] += 1
                    t['neutral_passes'] += float(bool(num(r,'pass_attempt') or num(r,'sack')))
            if r.get('yardline_100') in ('',None):
                continue
            yardline = num(r,'yardline_100')
            if num(r,'rush_attempt') and yardline <= 5 and r.get('rusher_player_id'):
                players[r['game_id']+'|'+r['rusher_player_id']]['goal_line_carries'] += 1
                t['goal_line_carries'] += 1
            if num(r,'pass_attempt') and r.get('receiver_player_id'):
                p = players[r['game_id']+'|'+r['receiver_player_id']]
                if yardline <= 20:
                    p['red_zone_targets'] += 1
                    t['red_zone_targets'] += 1
                if r.get('air_yards') not in ('',None) and num(r,'air_yards') >= yardline:
                    p['end_zone_targets'] += 1
                    t['end_zone_targets'] += 1
        return {'players':dict(players),'teams':dict(teams)}

    def slate(self, season, week):
        games = [g for g in self.games if g['season']==str(season) and g['week']==str(week) and g['game_type']=='REG']
        if not games:
            raise ValueError('Missing slate')
        from zoneinfo import ZoneInfo
        result = {}
        for g in games:
            kickoff = datetime.fromisoformat(g['gameday']+'T'+g['gametime']).replace(tzinfo=ZoneInfo('America/New_York'))
            for side,other in (('home','away'),('away','home')):
                club = team(g[side+'_team'])
                result[club] = {'game_id':g['game_id'],'opponent':team(g[other+'_team']),
                                'home':side=='home','kickoff':kickoff.isoformat()}
        return result

    def roster(self, season, week, slate, current=False):
        rows = self.rosters[season]
        if current:
            selected = [r for r in rows if r.get('status')=='ACT']
        else:
            # Roster status from a completed prior week; a target-week roster
            # could carry postgame transactions and is never a replay input.
            eligible = [r for r in rows if r.get('game_type')=='REG' and int(r.get('week') or 0)<week]
            latest = max((int(r['week']) for r in eligible),default=0)
            selected = [r for r in eligible if int(r['week'])==latest and r.get('status')=='ACT']
        found = {}
        for r in selected:
            club = team(r['team'])
            if r.get('position') not in POSITIONS or club not in slate:
                continue
            pid = r.get('gsis_id')
            if not pid:
                raise ValueError('Active offensive player lacks a stable id')
            if pid in found and found[pid]['team'] != club:
                raise ValueError(f'Ambiguous current team for {pid}')
            found[pid] = {'id':pid,'name':r['full_name'],'team':club,'position':r['position'],
                          'photo':r.get('headshot_url') or None,'pfr_id':r.get('pfr_id'),
                          'espn_id':r.get('espn_id')}
        if not found:
            raise ValueError('No strictly prior roster for evaluation')
        return sorted(found.values(),key=lambda r:r['id'])

    @staticmethod
    def before(row, season, week):
        return (row['season'],row['week']) < (season,week)

    def features(self, season, week, *, current=False, asof=None):
        cache_key = (season,week,current,asof)
        if cache_key in self._features:
            return self._features[cache_key]
        slate = self.slate(season,week)
        roster = self.roster(season,week,slate,current)
        if asof is None:
            asof = min(datetime.fromisoformat(g['kickoff']) for g in slate.values()).isoformat()
        cutoff = datetime.fromisoformat(asof.replace('Z','+00:00'))
        if season >= 2025:
            stamps = [s for s in self.depth[season] if datetime.fromisoformat(s.replace('Z','+00:00'))<cutoff]
        else:
            stamps = [s for s in self.depth[season] if s<week]
        depth_stamp = max(stamps,default=None)
        depths = {(r['id'],r['team']):r['rank'] for r in self.depth[season].get(depth_stamp,[])}
        prior_team = {club:[r for r in rows if r['season']>=season-2 and self.before(r,season,week)] for club,rows in self.teams.items()}
        prior_player = {pid:[r for r in rows if r['season']>=season-2 and self.before(r,season,week)] for pid,rows in self.player.items()}
        all_team = [r for rows in prior_team.values() for r in rows]
        if not all_team:
            raise ValueError('No historical team data')
        league = {k:average([r.get(k,0) for r in all_team]) for k in (*STATS,*CONTEXT)}
        league_rates = {}
        for pos in POSITIONS:
            group = [r for rows in prior_player.values() for r in rows if r['position']==pos]
            league_rates[pos] = {k:sum(r.get(k,0) for r in group) for k in STATS}
        # Observed workload-slot priors, learned only from past games. These
        # are broad priors for new players, not external player forecasts.
        slots = defaultdict(list)
        groups = defaultdict(list)
        for rows in prior_player.values():
            for r in rows:
                if r['position'] in POSITIONS:
                    groups[(r['game_id'],r['team'],r['position'])].append(r)
        for (game,club,pos),group in groups.items():
            totals = self.team_games.get((game,club),{})
            for key in ('carries','targets'):
                denominator = totals.get(key,0)
                if denominator <= 0:
                    continue
                ordered = sorted(group,key=lambda r:-r[key])
                for rank in range(1,8):
                    value = ordered[rank-1][key]/denominator if len(ordered)>=rank else 0.0
                    slots[(pos,key,rank)].append(value)
        role_priors = {key:average(values) for key,values in slots.items()}

        def summarize(rows):
            prior = [r for r in rows if r['season']<season][-12:]
            recent = [r for r in rows if r['season']==season]
            def stat_means(group):
                return {k:weighted_average([r.get(k,0) for r in group if k!='offense_pct' or k in r]) for k in (*STATS,*CONTEXT)}
            return {'prior':stat_means(prior),'recent':stat_means(recent),'prior_n':len(prior),
                    'recent_n':len(recent),'totals':{k:sum(r.get(k,0) for r in (prior+recent)[-16:]) for k in STATS},
                    'baseline':{fmt:average([score(r,rv) for r in rows[-8:]]) for fmt,rv in FORMATS.items()},
                    'prior_snap_n':sum('offense_pct' in r for r in prior),
                    'recent_snap_n':sum('offense_pct' in r for r in recent),
                    'last_week':max(((r['season'],r['week']) for r in rows),default=None)}

        teams = {}
        for club in slate:
            item = summarize(prior_team.get(club,[]))
            allowed = [r for r in all_team if r['opponent']==club]
            item['allowed'] = summarize(allowed)
            teams[club] = item
        features = []
        for p in roster:
            hist = prior_player.get(p['id'],[])
            item = summarize(hist)
            item.update(p)
            item['depth_rank'] = depths.get((p['id'],p['team']))
            shares = {'prior':{},'recent':{}}
            for period in shares:
                selected = ([r for r in hist if r['season']<season][-12:] if period=='prior'
                            else [r for r in hist if r['season']==season])
                for key in ('carries','targets','goal_line_carries','red_zone_targets','end_zone_targets'):
                    ratios=[]
                    for r in selected:
                        totals=self.team_games.get((r['game_id'],r['team']),{})
                        den=totals.get(key,0)
                        if den>0:
                            ratios.append(r.get(key,0)/den)
                    shares[period][key]=weighted_average(ratios) if ratios else None
            item['shares']=shares
            features.append(item)
        result={'season':season,'week':week,'asof':asof,'slate':slate,'players':features,'teams':teams,
                'league':league,'rates':league_rates,'role_priors':role_priors,'depth_asof':depth_stamp}
        self._features[cache_key]=result
        return result


def predict(features, config):
    """Predict counts and efficiencies, then reconcile the complete offense."""
    league=features['league'];teams=features['teams'];slate=features['slate']
    result=[];groups=defaultdict(list);budgets={}
    qb_roles={}
    for club in teams:
        available=[p for p in features['players'] if p['team']==club and p['position']=='QB' and not p.get('unavailable')]
        # Promote the next available QB when the first is confirmed unavailable.
        # A missing historical depth row falls back to strictly prior attempts,
        # never to the target game's starter or box score.
        available.sort(key=lambda p:(p['depth_rank'] or 99,-(p['recent']['attempts'] if p['recent_n'] else p['prior']['attempts']),p['id']))
        qb_roles.update({p['id']:rank for rank,p in enumerate(available,1)})

    def team_mean(item,key):
        prior=item['prior'][key] if item['prior_n'] else league.get(key,0)
        recent=item['recent'][key] if item['recent_n'] else None
        return blend(prior,recent,item['recent_n'],config['team_prior_games'])

    for club,t in teams.items():
        opponent=teams[slate[club]['opponent']]['allowed']
        plays=team_mean(t,'attempts')+team_mean(t,'carries')
        ordinary_pass=team_mean(t,'attempts')/max(1,plays)
        neutral=team_mean(t,'neutral_passes')/max(1,team_mean(t,'neutral_plays'))
        # Neutral tendencies supplement observed attempts; they include sacks,
        # so only their deviation from the league neutral rate is used.
        league_neutral=league['neutral_passes']/max(1,league['neutral_plays'])
        pass_rate=clamp(ordinary_pass+config['neutral_weight']*(neutral-league_neutral),.35,.75)
        opp_volume=(team_mean(opponent,'attempts')+team_mean(opponent,'carries'))/max(1,league['attempts']+league['carries'])
        plays*=1+config['opponent_weight']*(clamp(opp_volume,.8,1.2)-1)
        attempts=plays*pass_rate
        targets=attempts*clamp(team_mean(t,'targets')/max(1,team_mean(t,'attempts')),.8,1)
        budgets[club]={'attempts':attempts,'carries':plays-attempts,'targets':targets}
        for field,opportunity in (('passing_tds','attempts'),('rushing_tds','carries')):
            league_rate=league[field]/max(1,league[opportunity])
            observed_rate=team_mean(t,field)/max(1,team_mean(t,opportunity))
            opp_rate=team_mean(opponent,field)/max(1,team_mean(opponent,opportunity))
            rate=.75*observed_rate+.25*league_rate
            rate*=1+config['opponent_weight']*(clamp(opp_rate/max(.001,league_rate),.6,1.4)-1)
            budgets[club][field]=budgets[club][opportunity]*rate

    for p in features['players']:
        pos=p['position'];club=p['team'];rank=p['depth_rank'];b=budgets[club]
        qb_role=qb_roles.get(p['id'])
        stat={k:0.0 for k in STATS};shares={};lr=features['rates'][pos]
        for key in ('carries','targets','goal_line_carries','red_zone_targets','end_zone_targets'):
            seed_key='carries' if key in ('carries','goal_line_carries') else 'targets'
            slot=features['role_priors'].get((pos,seed_key,min(rank or 7,7)),0)
            prior=p['shares']['prior'][key]
            recent=p['shares']['recent'][key]
            prior=slot if prior is None else prior
            share=blend(prior,recent,p['recent_n'],config['player_prior_games'])
            if key in ('carries','targets') and p['prior']['offense_pct']>.05 and p['recent_snap_n']:
                ratio=clamp(p['recent']['offense_pct']/p['prior']['offense_pct'],.5,1.75)
                share*=ratio**config['snap_weight']
            shares[key]=max(0,share)
        stat['carries']=b['carries']*shares['carries']
        stat['targets']=b['targets']*shares['targets']
        if pos=='QB':
            stat['attempts']=b['attempts']*{1:.98,2:.018,3:.002}.get(qb_role,0)
            if qb_role != 1:
                # QB starter changes affect all QB opportunities together.
                stat['carries']*=.04 if qb_role==2 else .005
        total=p['totals']
        def rate(numerator,denominator,bound):
            league_rate=lr[numerator]/lr[denominator] if lr[denominator] else 0
            strength=config['efficiency_prior']*(2 if denominator=='attempts' else 1)
            return clamp((total[numerator]+league_rate*strength)/(total[denominator]+strength),0,bound)
        stat['completions']=stat['attempts']*rate('completions','attempts',1)
        stat['passing_yards']=stat['attempts']*rate('passing_yards','attempts',12)
        stat['passing_interceptions']=stat['attempts']*rate('passing_interceptions','attempts',.15)
        stat['rushing_yards']=stat['carries']*rate('rushing_yards','carries',9)
        stat['receptions']=stat['targets']*rate('receptions','targets',1)
        stat['receiving_yards']=stat['targets']*rate('receiving_yards','targets',18)
        high=config['scoring_opportunity_weight']
        stat['rushing_tds']=b['rushing_tds']*((1-high)*shares['carries']+high*shares['goal_line_carries'])
        if pos=='QB' and qb_role!=1:
            stat['rushing_tds']*=.04 if qb_role==2 else .005
        stat['receiving_tds']=b['passing_tds']*((1-high)*shares['targets']+high*(.5*shares['red_zone_targets']+.5*shares['end_zone_targets']))
        stat['passing_tds']=b['passing_tds']*{1:.98,2:.018,3:.002}.get(qb_role,0) if pos=='QB' else 0
        den=total['attempts']+total['carries']+total['targets']
        league_den=lr['attempts']+lr['carries']+lr['targets']
        fumrate=(total['fumbles_lost_total']+100*lr['fumbles_lost_total']/max(1,league_den))/(den+100)
        stat['fumbles_lost_total']=(stat['attempts']+stat['carries']+stat['targets'])*fumrate
        # Rare event rates are estimated across the position, not fabricated
        # as player-specific certainties after a one-game return touchdown.
        for field,opportunity in (('passing_2pt_conversions','attempts'),('rushing_2pt_conversions','carries'),('receiving_2pt_conversions','targets')):
            stat[field]=stat[opportunity]*lr[field]/max(1,lr[opportunity])
        unavailable=bool(p.get('unavailable'))
        if unavailable:
            stat={k:0.0 for k in STATS}
        row={'id':p['id'],'name':p['name'],'position':pos,'team':club,'stat_projection':stat,
             'depth_rank':rank,'projected_qb_role':qb_role,'unavailable':unavailable,'_fumrate':fumrate,
             'evidence':{'prior_appearances':p['prior_n'],'current_appearances':p['recent_n'],
                         'current_snap_pct':p['recent']['offense_pct'] if p['recent_snap_n'] else None,
                         'prior_snap_pct':p['prior']['offense_pct'] if p['prior_snap_n'] else None,
                         'last_stat_week':p['last_week'],'baseline':p['baseline'],
                         'goal_line_share':shares['goal_line_carries'],'red_zone_target_share':shares['red_zone_targets']}}
        result.append(row);groups[club].append(row)

    for club,group in groups.items():
        b=budgets[club]
        families={'attempts':('attempts','completions','passing_yards','passing_interceptions','passing_2pt_conversions'),
                  'carries':('carries','rushing_yards','rushing_2pt_conversions'),
                  'targets':('targets','receptions','receiving_yards','receiving_2pt_conversions')}
        for opportunity,fields in families.items():
            total=sum(p['stat_projection'][opportunity] for p in group)
            if total<=0 and b[opportunity]>0:
                raise ValueError(f'No {club} {opportunity} weights')
            factor=b[opportunity]/total if total else 0
            for p in group:
                for field in fields:p['stat_projection'][field]*=factor
        for field in ('passing_tds','rushing_tds','receiving_tds'):
            budget=b['passing_tds' if field=='receiving_tds' else field]
            total=sum(p['stat_projection'][field] for p in group)
            if total<=0 and budget>0:
                raise ValueError(f'No {club} {field} weights')
            for p in group:p['stat_projection'][field]*=budget/total if total else 0
        # Reconcile completions and receiving yards to the modeled QB offense.
        # Capped proportional reception allocation cannot exceed player targets.
        completion_total=sum(p['stat_projection']['completions'] for p in group)
        target_total=sum(p['stat_projection']['targets'] for p in group)
        completion_total=min(completion_total,target_total)
        original_completions=sum(p['stat_projection']['completions'] for p in group)
        for p in group:
            p['stat_projection']['completions']*=completion_total/max(.001,original_completions)
        remaining=completion_total;free=[p for p in group if p['stat_projection']['targets']>0]
        while free:
            weights=sum(p['stat_projection']['receptions'] for p in free)
            if weights<=0:raise ValueError('No receiving completion weights')
            over=[p for p in free if remaining*p['stat_projection']['receptions']/weights>p['stat_projection']['targets']]
            if not over:
                for p in free:p['stat_projection']['receptions']*=remaining/weights
                break
            for p in over:
                p['stat_projection']['receptions']=p['stat_projection']['targets']
                remaining-=p['stat_projection']['receptions'];free.remove(p)
        passing_yards=sum(p['stat_projection']['passing_yards'] for p in group)
        receiving_yards=sum(p['stat_projection']['receiving_yards'] for p in group)
        if receiving_yards<=0 and passing_yards>0:raise ValueError('Missing receiver yardage weights')
        for p in group:
            p['stat_projection']['receiving_yards']*=passing_yards/receiving_yards if receiving_yards else 0
            p['stat_projection']['fumbles_lost_total']=sum(p['stat_projection'][k] for k in ('attempts','carries','targets'))*p.pop('_fumrate')
            p['stat_projection']={k:round(v,4) for k,v in p['stat_projection'].items()}
            p['formats']={fmt:{'projected_points':round(score(p['stat_projection'],rv),1)} for fmt,rv in FORMATS.items()}
    for fmt in FORMATS:
        counts=defaultdict(int)
        for rank,p in enumerate(sorted(result,key=lambda p:(-score(p['stat_projection'],FORMATS[fmt]),p['id'])),1):
            counts[p['position']]+=1
            p['formats'][fmt].update(overall_rank=rank,position_rank=counts[p['position']])
    return {'players':result,'team_workload_budgets':budgets}
