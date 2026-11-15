from fastapi.testclient import TestClient
from mrfinancer.api import app
from test_business_plan import example

def test_proxy_forwards_user_token_through_both_gradio_requests(monkeypatch):
    import httpx
    from mrfinancer import business_plan

    seen = []
    def upstream(request):
        assert request.headers["Authorization"] == "Bearer hf_sample"
        assert request.url.host == "vivekchakraverty-mr-financer-business-plan.hf.space"
        seen.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={"event_id": "abcd1234"})
        return httpx.Response(200, content='event: complete\ndata: [{"ok":true,"sections":[]}]\n\n')

    client = httpx.AsyncClient
    monkeypatch.setattr(business_plan.httpx, "AsyncClient",
                        lambda **kw: client(transport=httpx.MockTransport(upstream), **kw))
    response = TestClient(app).post("/business-plan/draft", json=example(),
                                    headers={"Authorization": "Bearer hf_sample"})
    assert response.status_code == 200
    assert response.json()["ok"]
    assert seen == ["POST", "GET"]

