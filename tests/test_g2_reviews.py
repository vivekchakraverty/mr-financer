"""G2 integration tests; never contact live G2 or Hugging Face."""
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from mrfinancer.api import app
from mrfinancer import workspace, g2_reviews
from mrfinancer.config import Settings
from mrfinancer.models import ProductMatch
from mrfinancer.provenance import AccessMethod, Provenance, Trust


def product(**changes):
    return ProductMatch(**(dict(
        name='Invoicer', homepage='https://invoicer.example', kind='product',
        market='United States', provenance=Provenance(source='fixture',source_name='Fixture',
            method=AccessMethod.OFFICIAL_API, trust=Trust.OFFICIAL))|changes))


def fake_upstream(request):
    if request.url.host=='huggingface.co':
        user='alice' if request.headers['Authorization'].endswith('hf_a') else 'bob'
        return httpx.Response(200,json={'id':'123' if user=='alice' else '456','name':user,'type':'user'})
    assert request.url.host=='data.g2.com'
    assert request.headers['Authorization']=='Bearer test-operator'
    if request.url.path.endswith('/products'):
        assert request.url.params['filter[query]']=='Invoicer'
        return httpx.Response(200,json={'data':[{'id':'product-one','attributes':{
            'name':'Invoicer','domain':'invoicer.example','slug':'invoicer','review_count':40,'star_rating':4.2,
            'g2_url':'https://www.g2.com/products/invoicer/reviews'}}]})
    assert request.url.path.endswith('/products/product-one/reviews')
    return httpx.Response(200,json={'data':[
        {'attributes':{'title':'Delighted','star_rating':5,'love':'Easy','published_at':'2026-11-05T02:00:00Z','url':'https://www.g2.com/products/invoicer/reviews/pos'}},
        {'attributes':{'title':'Disappointed','star_rating':1,'hate':'Slow','published_at':'2026-11-06T02:00:00Z','url':'https://www.g2.com/products/invoicer/reviews/neg'}}]})


def test_ledger_atomic_limits_and_global_precedence(tmp_path):
    ledger=g2_reviews.G2Ledger(tmp_path/'g2.sqlite',global_limit=4)
    for _ in range(3): ledger.reserve('alice')
    assert ledger.status('alice')['remaining']==0
    with pytest.raises(g2_reviews.G2Error,match='all 3') as err:
        ledger.reserve('alice')
    assert err.value.code=='g2_personal_quota_exhausted'
    assert ledger.status('bob')['remaining']==3
    ledger.reserve('bob')
    assert ledger.status('bob')['remaining']==2
    assert ledger.status('bob')['global_exhausted']
    assert ledger.status('charlie')['used']==0
    with pytest.raises(g2_reviews.G2Error,match='global G2 API quota') as err:
        ledger.reserve('charlie')
    assert err.value.code=='g2_global_quota_exhausted'
    assert ledger.status('alice')['used']==3


def test_month_change_resets_user_counters_and_global(tmp_path,monkeypatch):
    ledger=g2_reviews.G2Ledger(tmp_path/'g2.sqlite',3)
    monkeypatch.setattr(g2_reviews,'month_key',lambda now=None:'2026-11')
    ledger.reserve('alice')
    ledger.mark_global_exhausted()
    assert ledger.status('bob')['global_exhausted']
    monkeypatch.setattr(g2_reviews,'month_key',lambda now=None:'2026-12')
    assert ledger.status('alice')['remaining']==3
    assert not ledger.status('bob')['global_exhausted']
    assert ledger.status('alice')['reset_date']=='2027-01-01'


@pytest.mark.asyncio
async def test_fetch_g2_products_and_reviews_count_as_two_calls(tmp_path,monkeypatch):
    monkeypatch.setenv('g2_api','test-operator')
    settings=Settings(g2_usage_db=tmp_path/'g2.sqlite',g2_global_monthly_limit=50)
    ledger=g2_reviews.G2Ledger(settings.g2_usage_db,settings.g2_global_monthly_limit)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream)) as client:
        result=await g2_reviews.fetch_g2(product(),'https://invoicer.example',settings,'hf_a',client=client,ledger=ledger)
    assert result['quota']['used']==2
    assert result['quota']['remaining']==1
    assert result['feed']['average']==4.2
    assert [r['rating'] for r in result['feed']['reviews']]==[1,5]
    assert result['feed']['reviews'][0]['title']=='Disappointed'
    # One remaining call fetches product rating, then stops safely before requesting reviews.
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream)) as client:
        result=await g2_reviews.fetch_g2(product(),'https://invoicer.example',settings,'hf_a',client=client,ledger=ledger)
    assert result['quota']['used']==3
    assert result['feed']['reviews']==[]
    with pytest.raises(g2_reviews.G2Error) as err:
        async with httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream)) as client:
            await g2_reviews.fetch_g2(product(),'https://invoicer.example',settings,'hf_a',client=client,ledger=ledger)
    assert err.value.code=='g2_personal_quota_exhausted'


@pytest.mark.asyncio
async def test_global_quota_error_for_new_user_who_has_zero_calls(tmp_path,monkeypatch):
    monkeypatch.setenv('g2_api','test-operator')
    settings=Settings(g2_usage_db=tmp_path/'g2.sqlite',g2_global_monthly_limit=2)
    ledger=g2_reviews.G2Ledger(settings.g2_usage_db,2)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream)) as client:
        result=await g2_reviews.fetch_g2(product(),'https://invoicer.example',settings,'hf_a',client=client,ledger=ledger)
        assert result['quota']['global_exhausted']
        with pytest.raises(g2_reviews.G2Error) as err:
            await g2_reviews.fetch_g2(product(),'https://invoicer.example',settings,'hf_b',client=client,ledger=ledger)
    assert err.value.code=='g2_global_quota_exhausted'
    assert ledger.status('hf:456')['used']==0


@pytest.mark.asyncio
async def test_bad_identity_or_wrong_product_never_retrieves_review(tmp_path,monkeypatch):
    monkeypatch.setenv('g2_api','test-operator')
    settings=Settings(g2_usage_db=tmp_path/'g2.sqlite')
    ledger=g2_reviews.G2Ledger(settings.g2_usage_db,1000)
    async with httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream)) as client:
        with pytest.raises(g2_reviews.G2Error) as err:
            await g2_reviews.fetch_g2(product(),'https://invoicer.example',settings,'',client=client,ledger=ledger)
        assert err.value.code=='hf_login_required'
    assert ledger.status('hf:123')['used']==0
    # Product mismatch is prevented even if fuzzy search returned a name collision.
    found=g2_reviews.selected_product({'data':[{'id':'other','attributes':{'name':'Other Product','domain':'other.example'}}]},
                                     product(),'https://invoicer.example')
    assert found is None


def test_server_route_does_not_affect_other_review_or_plan_modules(monkeypatch):
    key='x'*24
    monkeypatch.setattr(workspace,'searches',{key:(time.monotonic(),[product()])})
    client=TestClient(app)
    response=client.post('/idea/company-research/g2',json={'search_id':key,'index':0})
    assert response.status_code==401
    assert response.json()['detail']['code']=='hf_login_required'
    assert client.get('/assets/company-research.js').status_code==200
    assert 'href="#business-plan"' in client.get('/').text
