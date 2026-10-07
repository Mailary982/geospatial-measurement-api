from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.error_handlers import register_exception_handlers
from app.core.exceptions import FileTooLargeError


def build_app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/app-error")
    def app_error():
        raise FileTooLargeError("File exceeds the 20 MB upload limit.")

    @app.get("/crash")
    def crash():
        raise RuntimeError("secret internal detail")

    @app.get("/items/{item_id}")
    def item(item_id: int):
        return {"item_id": item_id}

    return app


client = TestClient(build_app(), raise_server_exceptions=False)


def test_app_error_uses_common_shape():
    response = client.get("/app-error")

    assert response.status_code == 413
    assert response.json() == {
        "error": {"code": "FILE_TOO_LARGE", "message": "File exceeds the 20 MB upload limit.", "details": None}
    }


def test_request_validation_error_uses_common_shape():
    response = client.get("/items/not-a-number")

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert error["details"][0]["loc"] == ["path", "item_id"]


def test_unknown_route_uses_common_shape():
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "HTTP_ERROR"


def test_unexpected_error_hides_internal_details():
    response = client.get("/crash")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text
