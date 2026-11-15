"""Explicit, quota-metered G2 product/reviews integration (not business-plan inference).

A signed-in Hugging Face user identity is used only to enforce per-user quotas:
no Hugging Face token is stored and G2 uses the operator's server-side API token.
"""
from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, UTC
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .company_crawl import clean, site_host, web_url
from .company_reviews import normalized, provider, row, stars

G2_ROOT = 'https://data.g2.com/api/v2'
HF_WHOAMI = 'https://huggingface.co/api/whoami-v2'
USER_LIMIT = 3  # fixed public-facing policy, never overridable via request


class G2Error(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def month_key(now=None):
    return (now or datetime.now(UTC)).strftime('%Y-%m')


def reset_date(month: str):
    year, number = map(int, month.split('-'))
    return f'{year + (number == 12):04d}-{(number % 12) + 1:02d}-01'


class G2Ledger:
    """SQLite BEGIN IMMEDIATE serializes reservations across workers on one shared filesystem.

    Never use an ephemeral or per-worker DB for a multi-user production service.
    For multiple machines, replace this with a central transactional database.
    """
    def __init__(self, path: Path, global_limit: int):
        self.path = Path(path)
        self.global_limit = global_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS g2_user (month TEXT NOT NULL, user_id TEXT NOT NULL, calls INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(month,user_id))')
            db.execute('CREATE TABLE IF NOT EXISTS g2_global (month TEXT PRIMARY KEY, calls INTEGER NOT NULL DEFAULT 0, exhausted INTEGER NOT NULL DEFAULT 0)')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        db.execute('PRAGMA busy_timeout=15000')
        return db

    def _status(self, db, user_id, month):
        global_row = db.execute('SELECT calls,exhausted FROM g2_global WHERE month=?', (month,)).fetchone() or (0,0)
        user_row = db.execute('SELECT calls FROM g2_user WHERE month=? AND user_id=?', (month,user_id)).fetchone()
        user_used = user_row[0] if user_row else 0
        return dict(month=month, reset_date=reset_date(month), remaining=max(0, USER_LIMIT-user_used),
                    used=user_used, global_remaining=max(0,self.global_limit-global_row[0]),
                    global_exhausted=bool(global_row[1] or global_row[0]>=self.global_limit))

    def status(self, user_id):
        with self.connect() as db:
            return self._status(db,user_id,month_key())

    def reserve(self, user_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            month=month_key()
            state=self._status(db,user_id,month)
            if state['global_exhausted']:
                db.execute('COMMIT')
                raise G2Error('g2_global_quota_exhausted',
                              'The global G2 API quota of calls is finished for this month. This affects all users, even those with unused personal calls.',429)
            if state['remaining']==0:
                db.execute('COMMIT')
                raise G2Error('g2_personal_quota_exhausted',
                              f'You have used all 3 of your G2 API calls this month. Your allowance resets {state["reset_date"]} (UTC).',429)
            db.execute('INSERT INTO g2_global(month,calls) VALUES (?,1) ON CONFLICT(month) DO UPDATE SET calls=calls+1',(month,))
            db.execute('INSERT INTO g2_user(month,user_id,calls) VALUES (?,?,1) ON CONFLICT(month,user_id) DO UPDATE SET calls=calls+1',(month,user_id))
            state=self._status(db,user_id,month)
            db.execute('COMMIT')
            return state

    def mark_global_exhausted(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO g2_global(month,calls,exhausted) VALUES (?,0,1) ON CONFLICT(month) DO UPDATE SET exhausted=1',(month_key(),))
            db.execute('COMMIT')


def token_from_settings(settings):
    # Both operators' environment naming conventions are supported. No token ever in client JS.
    return os.getenv('g2_api') or settings.g2_api_key


async def hf_identity(client, token):
    if not token or len(token)>2048:
        raise G2Error('hf_login_required','Sign in with a personal Hugging Face access token to use the G2 three-call allowance.',401)
    try:
        response=await client.get(HF_WHOAMI,headers={'Authorization':f'Bearer {token}'},timeout=8)
    except httpx.RequestError:
        raise G2Error('hf_unavailable','Hugging Face account verification is temporarily unavailable.',503) from None
    if response.status_code in (401,403):
        raise G2Error('hf_invalid','Hugging Face rejected your identity token. Check that it is valid.',401)
    if response.status_code>=400:
        raise G2Error('hf_unavailable','Hugging Face account verification is temporarily unavailable.',503)
    try:
        data=response.json()
    except ValueError:
        raise G2Error('hf_unavailable','Hugging Face returned an unexpected account response.',503) from None
    if not isinstance(data,dict):
        raise G2Error('hf_unavailable','Hugging Face returned an unexpected account response.',503)
    if data.get('type')!='user' or not (data.get('id') or data.get('name')):
        raise G2Error('hf_user_required','Use a personal Hugging Face account to enforce the three-call limit.',403)
    return 'hf:' + str(data.get('id') or data['name'])


def likely_monthly_limit(response):
    """Do not mistake G2's short-term 100 req/s Cloudflare 429 for monthly exhaustion."""
    if response.status_code not in (403, 429):
        return False
    try:
        body=response.text[:1000].lower()
    except Exception:
        body=''
    limit_marker=' '.join([response.headers.get('x-ratelimit-reset',''),response.headers.get('x-rate-limit-period',''),body]).lower()
    return ('monthly' in limit_marker or 'month quota' in limit_marker or
            'quota exhausted' in limit_marker or 'quota exceeded' in limit_marker)


async def call_g2(client, *, ledger, user_id, api_token, path, params):
    # Reserve before the outbound request. Failed/unknown attempts conservatively count.
    state=ledger.reserve(user_id)
    try:
        response=await client.get(G2_ROOT+path,params=params,headers={
            'Authorization':f'Bearer {api_token}', 'Accept':'application/vnd.api+json'
        },timeout=10)
    except httpx.RequestError:
        raise G2Error('g2_unavailable','G2 could not be reached. This attempted request used one G2 call.',503) from None
    if likely_monthly_limit(response):
        ledger.mark_global_exhausted()
        raise G2Error('g2_global_quota_exhausted','The global G2 API quota of calls is finished. This affects everyone, even with unused personal calls.',429)
    if response.status_code==429:
        raise G2Error('g2_temporarily_limited','G2 is temporarily rate-limiting our shared API access. Try again later. This request counted toward your allowance.',429)
    if response.status_code in (401,403):
        raise G2Error('g2_permissions','The operator\'s G2 API token is invalid or does not have permission to read products and reviews. This attempted request counted toward your allowance.',502)
    if response.status_code>=400:
        raise G2Error('g2_request_failed',f'G2 returned HTTP {response.status_code}. This attempted request counted toward your allowance.',502)
    try:
        return response.json(),state
    except ValueError:
        raise G2Error('g2_invalid_response','G2 returned an unexpected response; the request counted toward your allowance.',502) from None


def selected_product(data, product, website):
    """Reject ambiguous matches: no attaching another vendor's reviews by name alone."""
    domain=site_host(website) if website else None
    rows=[]
    for item in data.get('data',[]):
        if not isinstance(item,dict):
            continue
        attr=item.get('attributes') or {}
        if not isinstance(attr,dict):
            continue
        label=clean(attr.get('name'),160)
        candidate_domain=attr.get('domain')
        if not candidate_domain:
            candidate_domain=site_host(attr.get('public_detail_url') or '')
        else:
            candidate_domain=site_host('https://'+str(candidate_domain))
        same_domain=bool(domain and candidate_domain==domain)
        same_name=bool(label and normalized(label)==normalized(product.name))
        if not (same_name or same_domain):
            continue
        # Unique, verified identity required, and official domain takes precedence.
        rows.append((2 if same_name and same_domain else 1 if same_domain else 0,item,attr))
    if not rows:
        return None
    high=max(item[0] for item in rows)
    matches=[r for r in rows if r[0]==high]
    if len(matches)!=1:
        return None
    _,hit,attr=matches[0]
    uid=hit.get('id')
    if not isinstance(uid,str) or not re.fullmatch(r'[\w-]{1,120}',uid,re.ASCII):
        return None
    return uid,attr


def parse_g2_reviews(data):
    results=[]
    for entry in data.get('data',[]):
        if not isinstance(entry,dict):
            continue
        attr=entry.get('attributes') or {}
        if not isinstance(attr,dict):
            continue
        rating=attr.get('star_rating')
        if not stars(rating):
            continue
        phrases=[]
        for key in ('love','hate','benefits','recommendations'):
            val=attr.get(key)
            if isinstance(val,str) and val.strip():
                phrases.append((key.title()+': '+val.strip())[:1600])
        raw_url=attr.get('url') or attr.get('g2_url')
        results.append(row(rating,title=attr.get('title') or '',text='\n'.join(phrases),
                           date=str(attr.get('published_at') or attr.get('submitted_at') or '')[:10],
                           link=raw_url))
    return results


async def fetch_g2(product, website, settings, hf_token, *, client=None, ledger=None):
    api_token=token_from_settings(settings)
    if not api_token:
        raise G2Error('g2_not_configured','The G2 API has not been configured by the operator.',503)
    ledger=ledger or G2Ledger(settings.g2_usage_db,settings.g2_global_monthly_limit)
    owns_client=client is None
    if owns_client:
        client=httpx.AsyncClient(follow_redirects=False)
    try:
        user_id=await hf_identity(client,hf_token)
        state=ledger.status(user_id)
        if state['global_exhausted']:
            raise G2Error('g2_global_quota_exhausted','The global G2 API quota of calls is finished for this month. This affects all users, even those with unused personal calls.',429)
        if state['remaining']==0:
            raise G2Error('g2_personal_quota_exhausted',f'You have used all 3 G2 API calls this month. Your allowance resets {state["reset_date"]} (UTC).',429)
        # One products call, then one reviews call only for a uniquely matched product.
        data,_=await call_g2(client,ledger=ledger,user_id=user_id,api_token=api_token,path='/products',
                             params={'filter[query]':product.name,'page[size]':25})
        matched=selected_product(data,product,website)
        if not matched:
            return {'feed':provider('G2','https://www.g2.com/','Product reviews','unavailable',
                'No unique G2 product matched the selected firm. One G2 API call was used.'),
                'quota':ledger.status(user_id)}
        identity,attr=matched
        url=web_url(attr.get('g2_url')) or web_url(attr.get('g2crowd_url'))
        if not url or urlsplit(url).hostname not in ('www.g2.com','g2.com'):
            slug=attr.get('slug')
            url=f'https://www.g2.com/products/{slug}/reviews' if isinstance(slug,str) and re.fullmatch(r'[a-z0-9-]{1,120}',slug) else 'https://www.g2.com/'
        rating=attr.get('star_rating')
        summary=provider('G2',url,'Product reviews','ok',
            'G2 product rating verified. A review sample is sorted lowest to highest, not the lowest-rated reviews across all G2.',
            scale=5, average=rating if stars(rating) else None,
            count=attr.get('review_count') if isinstance(attr.get('review_count'),int) else None)
        if ledger.status(user_id)['remaining']<1:
            summary['note']='G2 product rating found, but one more API call is needed for written reviews. Your monthly allowance is exhausted.'
            return {'feed':summary,'quota':ledger.status(user_id)}
        try:
            reviews_data,_=await call_g2(client,ledger=ledger,user_id=user_id,api_token=api_token,
                                        path=f'/products/{identity}/reviews',params={'page[size]':25})
        except G2Error as exc:
            summary['note']='G2 product rating found, but written reviews could not be retrieved: '+exc.message
            return {'feed':summary,'quota':ledger.status(user_id),'warning_code':exc.code}
        summary['reviews']=sorted(parse_g2_reviews(reviews_data),key=lambda r:r['rating'])
        return {'feed':summary,'quota':ledger.status(user_id)}
    finally:
        if owns_client:
            await client.aclose()
