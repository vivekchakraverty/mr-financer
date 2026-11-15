import base64
import json

import httpx
from fastapi.testclient import TestClient

from mrfinancer.api import app
from mrfinancer import pitch_art

ENDPOINT = "/business-plan/pitch/image"


def test_image_jobs_use_callers_token_on_submission_and_result(monkeypatch):
    requests = []
    jpeg = b"\xff\xd8\xffsample\xff\xd9"
    encoded = base64.b64encode(jpeg).decode()

    def upstream(request):
        requests.append((request.method, request.headers["authorization"]))
        assert request.url.host == "vivekchakraverty-mr-financer-pitch-images.hf.space"
        if request.method == "POST":
            assert request.url.path.endswith("/generate_pitch_image")
            assert json.loads(request.content) == {"data": ["Editorial illustration"]}
            return httpx.Response(200, json={"event_id": "job123"})
        assert request.url.path.endswith("/generate_pitch_image/job123")
        return httpx.Response(200, text="event: heartbeat\ndata: null\n\nevent: complete\ndata: " +
                              json.dumps([{"ok": True, "image_base64": encoded}]) + "\n\n")

    client = httpx.AsyncClient
    monkeypatch.setattr(pitch_art.httpx, "AsyncClient",
                        lambda **kwargs: client(transport=httpx.MockTransport(upstream), **kwargs))
    response = TestClient(app).post(ENDPOINT, json={"prompt": "Editorial illustration"},
                                    headers={"Authorization": "Bearer hf_pitch_user"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    assert response.content == jpeg
    assert requests == [("POST", "Bearer hf_pitch_user"), ("GET", "Bearer hf_pitch_user")]


def test_tokenless_and_invalid_images_are_rejected():
    assert TestClient(app).post(ENDPOINT, json={"prompt": "Editorial illustration"}).status_code == 401
    assert TestClient(app).post(ENDPOINT, json={"prompt": " "}).status_code == 422
    assert pitch_art.upstream_failure(502, {"error": "ZeroGPU quota exceeded"}).status_code == 429
    assert pitch_art.upstream_failure(403, {}).status_code == 403

