"""Scheduled, source-grounded collector. No LLM calls, logins or broker orders."""
import argparse
import hashlib
import json
import os
import math
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
import requests

AGENT = 'ProjectTResearchMonitor/1.0'
SOURCES = [
    dict(id='holguin-history', trader='Alfredo Holguin', url='https://holguintrades.com/stats/trade-history', kind='availability',
         note='Public history availability only. No trade data copied: source terms restrict storage without consent. Live SMS access is not connected.'),
    dict(id='carter-rss', trader='John Carter', url='https://www.simplertrading.com/feed/', kind='rss',
         note='Official team publication feed. Items are not attributed to Carter unless explicitly authored by him; not a live trade service.'),
    dict(id='carter-blog', trader='John Carter', url='https://www.simplertrading.com/blog', kind='links', path='/blog/',
         note='Official team article links. Education and research only; team articles are not Carter trade executions.'),
    dict(id='saliba-news', trader='Tony Saliba', url='https://www.liquidmercury.com/news/rss.xml', kind='rss',
         note='Official company news metadata, not Saliba personal trades.'),
    dict(id='sosnoff-appearances', trader='Tom Sosnoff', url='https://www.moneyshow.com/expert/e9e385ec29154496a19c8490b9fad291/', kind='links', path='/video/',
         note='Public appearance links, not a current live trade feed. Undated videos stay undated.'),
    dict(id='bruce-free-updates', trader='Bruce Marshall', url='https://www.simplertrading.com/daily/options-free', kind='links', path='/daily/options-free/',
         note='Official team free-video links; author attribution is not established by this listing. Not Bruce personal executions or holdings.'),
    dict(id='cnbc-club-rss', trader='CNBC Investing Club', url='https://www.cnbc.com/id/19854910/device/rss/rss.html', kind='rss', item_path='/investingclub/',
         note='Official CNBC RSS filtered to Investing Club. May be access-blocked. Full Trust portfolio is a member feature; no bypass.'),
]

def utcnow():
    return datetime.now(timezone.utc).isoformat()

class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.href, self.text = [], None, []
    def handle_starttag(self, tag, attrs):
        if tag == 'a':
            self.href, self.text = dict(attrs).get('href'), []
    def handle_data(self, data):
        if self.href:
            self.text.append(data)
    def handle_endtag(self, tag):
        if tag == 'a' and self.href:
            self.links.append((self.href, ' '.join(' '.join(self.text).split())))
            self.href = None

def safe_link(base, link):
    value = urljoin(base, link).split('#')[0]
    a, b = urlparse(base), urlparse(value)
    if b.scheme != 'https' or b.hostname != a.hostname or b.username or b.password:
        return None
    return value

def item(source, link, title, published=None, author=None):
    link = safe_link(source['url'], link)
    if not link:
        return None
    return dict(id=hashlib.sha256((source['id']+'|'+link).encode()).hexdigest()[:24],
                source_id=source['id'], trader=source['trader'], source_url=link,
                title=title[:180], published_utc=published, author=author,
                classification='PUBLICATION / NOT A TRADE ALERT', copy_ready=False)

def extract(source, content):
    if source['kind'] == 'availability':
        return []
    result = []
    if source['kind'] == 'rss':
        root = ET.fromstring(content)
        if root.tag != 'rss':
            raise ValueError('Expected official RSS; received another format')
        for entry in root.findall('./channel/item')[:50]:
            if source.get('item_path') and not urlparse(entry.findtext('link') or '').path.startswith(source['item_path']):
                continue
            published = None
            date = entry.findtext('pubDate')
            if date:
                try:
                    dt = parsedate_to_datetime(date)
                    if dt.tzinfo:
                        published = dt.astimezone(timezone.utc).isoformat()
                except (ValueError, TypeError):
                    pass
            record = item(source, entry.findtext('link') or '', entry.findtext('title') or 'Untitled publication',
                          published, entry.findtext('{http://purl.org/dc/elements/1.1/}creator'))
            if record:
                result.append(record)
    else:
        parser = Links()
        parser.feed(content)
        for link, title in parser.links:
            path = urlparse(urljoin(source['url'], link)).path
            if not title or not path.startswith(source['path']) or path.rstrip('/') == source['path'].rstrip('/'):
                continue
            record = item(source, link, title)
            if record:
                result.append(record)
    unique={}
    for row in result:
        if row['id'] not in unique or len(row['title'])>len(unique[row['id']]['title']):
            unique[row['id']]=row
    return list(unique.values())[:50]

def fetch_source(source, session):
    """Only fixed first-party URLs, with robots and redirect checks; no auth or bypass."""
    base = urlparse(source['url'])
    robots_url = f'{base.scheme}://{base.netloc}/robots.txt'
    robots = session.get(robots_url, timeout=20, allow_redirects=False)
    if robots.status_code == 200:
        rules = RobotFileParser()
        rules.parse(robots.text.splitlines())
        if not rules.can_fetch(AGENT, source['url']):
            raise ValueError('Collection disallowed by robots.txt')
        delay = rules.crawl_delay(AGENT) or 1
        time.sleep(min(max(delay, 1), 5))
        if delay > 5:
            raise ValueError('Source crawl delay exceeds this run budget')
    elif robots.status_code != 404:
        raise ValueError(f'Cannot establish robots access (HTTP {robots.status_code})')
    url = source['url']
    for _ in range(4):
        response = session.get(url, timeout=25, allow_redirects=False)
        if response.status_code in (301,302,303,307,308):
            url = safe_link(source['url'], response.headers.get('Location',''))
            if not url:
                raise ValueError('Cross-domain redirect refused')
            continue
        response.raise_for_status()
        if len(response.content) > 6_000_000:
            raise ValueError('Source response too large')
        if any(marker in response.text.lower() for marker in ['just a moment...', 'verify you are human']):
            raise ValueError('Source requires human access; no bypass attempted')
        return response.text
    raise ValueError('Too many redirects')

def collect(previous=None, fetcher=None):
    previous = previous or {}
    observed = utcnow()
    records = {r['id']: r for r in previous.get('items', [])}
    statuses, new_count = [], 0
    session = requests.Session()
    session.headers['User-Agent'] = AGENT
    for source in SOURCES:
        status = dict(source_id=source['id'], trader=source['trader'], url=source['url'],
                      checked_utc=observed, note=source['note'], status='error', items_returned=0)
        try:
            content = fetcher(source) if fetcher else fetch_source(source, session)
            found = extract(source, content)
            status.update(status='available' if source['kind']=='availability' else ('ok' if found else 'empty'), items_returned=len(found))
            for record in found:
                old = records.get(record['id'])
                record.update(first_seen_utc=old['first_seen_utc'] if old else observed,
                              last_seen_utc=observed,
                              baseline=old.get('baseline', False) if old else not bool(previous.get('last_run_utc')))
                if not old:
                    new_count += 1
                records[record['id']] = record
        except (requests.RequestException, ValueError, ET.ParseError) as exc:
            # Do not disclose response bodies, cookies or request headers.
            status['error'] = type(exc).__name__ + ': source unavailable or format/access changed'
        statuses.append(status)
    alerts={r['id']:r for r in previous.get('trade_alerts',[])}
    for record in records.values():
        candidate=trade_related_update(record)
        if candidate:
            alerts[candidate['id']]=candidate
    return dict(schema_version=1, last_run_utc=observed, expected_interval_minutes=15,
                new_items_this_run=new_count, sources=statuses,
                items=sorted(records.values(), key=lambda r:r['first_seen_utc'], reverse=True)[:500],
                current_positions=[], trade_alerts=list(alerts.values()),
                holdings_coverage={trader:dict(status='NOT VERIFIED',as_of_utc=None,complete=False,
                    reason='No current complete authorized position disclosure is connected. This does not mean the trader holds no positions.') for trader in dict.fromkeys(s['trader'] for s in SOURCES)},
                live_alerts_connected=False,
                live_route='Official X API for verified accounts or publisher-authorized subscriber feed; requires explicit access/budget approval.')

def trade_related_update(record):
    """Headline triage only; a portfolio update is not evidence of a completed transaction."""
    if not record.get('published_utc'):
        return None
    owner=record['trader']
    if owner!='CNBC Investing Club' and (record.get('author') or '').strip().lower()!=owner.lower():
        return None
    title=record['title'].lower().replace('’',"'")
    pattern=r"\b(?:we(?:'re| are)?|i(?:'m| am)?)\s+(?:buying|selling|bought|sold|adding|trimming|closing|opening|adjusting)\b|\b(?:buy|sell|trade)\s+alert\b"
    if not re.search(pattern,title):
        return None
    return dict(id=record['id'],trader=owner,source_url=record['source_url'],title=record['title'],
                published_utc=record['published_utc'],observed_utc=record['first_seen_utc'],
                baseline=record.get('baseline',False),
                classification='POTENTIAL TRADE / PORTFOLIO UPDATE — DETAILS UNVERIFIED',
                actual_transaction_confirmed=False,copy_ready=False,
                alert_state='BASELINE / NOT A NEW NOTIFICATION' if record.get('baseline') else 'NEW SOURCE UPDATE / REVIEW',
                contract=None,quantity=None,price=None)

def parse_signal(text):
    """Conservative extraction for authorized alerts. Never invent price, quantity or timezone."""
    action = re.search(r'\b(BTO|STC|STO|BTC|buy to open|sell to close|sell to open|buy to close)\b', text, re.I)
    contract = re.search(r'\b([A-Z]{1,6})\s+(\d{4}-\d{2}-\d{2})\s+(\d+(?:\.\d+)?)\s+([CP])\b', text)
    price = re.search(r'@\s*\$?(\d+(?:\.\d+)?)', text)
    if not action or not contract:
        return dict(classification='REQUIRES HUMAN PARSING', copy_ready=False)
    root, expiry, strike, right = contract.groups()
    try:
        datetime.strptime(expiry, '%Y-%m-%d')
    except ValueError:
        return dict(classification='INVALID CONTRACT DATE', copy_ready=False)
    action = {'BUY TO OPEN':'BTO','SELL TO CLOSE':'STC','SELL TO OPEN':'STO','BUY TO CLOSE':'BTC'}.get(action[0].upper(),action[0].upper())
    return dict(classification='SIGNAL CANDIDATE / REVIEW REQUIRED', copy_ready=False,
                action=action, contract=f'{root} {expiry} {strike} {right}',
                signal_limit=price[1] if price else None, quantity=None,
                reason='Source intent, timestamp, all legs, quote, sizing and risk must be verified. No automatic execution.')

def collect_x_private(output, approved=False):
    """Official API adapter, disabled unless separately approved; private local output only."""
    if not approved or os.environ.get('PROJECT_T_X_APPROVED') != 'yes':
        raise ValueError('Official X collection requires explicit access and spending approval first')
    token = os.environ.get('PROJECT_T_X_BEARER_TOKEN')
    if not token:
        raise ValueError('Approved X access has not been configured')
    # Only accounts linked by the traders’ official websites. No guessed handles.
    query='(from:HolguinTrades OR from:johnfcarter) -is:retweet'
    session=requests.Session()
    session.headers.update(Authorization='Bearer '+token, **{'User-Agent':AGENT})
    params={'query':query,'max_results':10,'tweet.fields':'created_at,author_id'}
    path=Path(output)
    old=json.loads(path.read_text()) if path.exists() else {'posts':[], 'estimated_read_cost_usd':0}
    # This counter is a conservative per-state-file budget; console spending limits remain mandatory.
    limit=float(os.environ.get('PROJECT_T_X_TOTAL_BUDGET_USD','0'))
    if not math.isfinite(limit) or limit <= 0 or old['estimated_read_cost_usd']+.05 > limit:
        raise ValueError('Approved X read budget exhausted or missing')
    if old.get('newest_id'):
        params['since_id']=old['newest_id']
    # Reserve the worst-case response charge BEFORE dispatch, including failed/uncertain responses.
    old['estimated_read_cost_usd'] += .05
    path.write_text(json.dumps(old,indent=2),encoding='utf-8')
    r=session.get('https://api.x.com/2/tweets/search/recent',params=params,timeout=20)
    if r.status_code!=200:
        raise ValueError(f'Official X API returned HTTP {r.status_code}; details omitted')
    payload=r.json()
    if payload.get('meta',{}).get('next_token'):
        raise ValueError('More than 10 new posts: gap detected. Reconcile before advancing cursor')
    posts={p['id']:p for p in old['posts']}
    for post in payload.get('data',[]):
        if not post.get('created_at') or not post.get('author_id'):
            continue
        posts[post['id']]={'id':post['id'], 'source_url':'https://x.com/i/web/status/'+post['id'],
                          'published_utc':post['created_at'], 'received_utc':utcnow(),
                          'author_id':post['author_id'], **parse_signal(post['text'])}
    old.update(posts=list(posts.values()), newest_id=payload.get('meta',{}).get('newest_id',old.get('newest_id')),
               last_run_utc=utcnow())
    # Raw posts and credentials never written. This file is private and must not be committed.
    path.write_text(json.dumps(old,indent=2),encoding='utf-8')
    return old

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',default='trader_source_state.json')
    parser.add_argument('--x-private',action='store_true')
    parser.add_argument('--approved',action='store_true')
    args=parser.parse_args()
    if args.x_private:
        collect_x_private(args.state,args.approved)
        print('Official X collection completed to private local state')
        return
    path=Path(args.state)
    previous=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
    state=collect(previous)
    path.write_text(json.dumps(state,indent=2),encoding='utf-8')
    print(json.dumps({'checked':len(state['sources']),'items':len(state['items']),
                      'new':state['new_items_this_run'],'statuses':[(s['source_id'],s['status']) for s in state['sources']]}))

if __name__=='__main__':
    main()
