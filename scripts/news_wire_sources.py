"""Publisher metadata only; no changes to historical commentary authority."""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
from wire.registry import load

CONFIG = Path(__file__).resolve().parents[1] / 'sources/news_wire.json'

@dataclass
class NewsSource:
    source_id: str
    source_name: str
    teams: list
    domains: list
    feed_url: str
    filter_url_pattern: str = ''
    filter_author: str = ''
    filter_categories: list = field(default_factory=list)
    path_teams: dict = field(default_factory=dict)
    team_owned: bool = False

    def owns(self, url):
        host = urlsplit(url).hostname or ''
        return any(host == d or host == 'www.' + d for d in self.domains)

    def teams_for(self, url):
        if self.path_teams:
            path = urlsplit(url).path
            return [team for prefix, team in self.path_teams.items() if path.startswith(prefix)]
        return self.teams

def sources():
    result = [NewsSource(s.source_id, s.source_name, s.teams, s.domains,
                         s.feed_url, s.filter_url_pattern, s.filter_author,
                         s.filter_categories)
              for s in load() if s.pollable and s.feed_url and not s.paid]
    for row in json.loads(CONFIG.read_text())['sources']:
        result.append(NewsSource(**row))
    if len({s.source_id for s in result}) != len(result):
        raise ValueError('Duplicate news source')
    for s in result:
        if not s.owns(s.feed_url) or not s.feed_url.startswith('https://'):
            raise ValueError('Invalid news feed ownership')
        if s.filter_url_pattern:
            re.compile(s.filter_url_pattern)
    return result
