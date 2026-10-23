import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.main import app


@pytest.fixture
def fastapi_app() -> FastAPI:
    return app


@pytest.fixture
async def async_client(fastapi_app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=fastapi_app), base_url="http://test"
    ) as client:
        yield client
