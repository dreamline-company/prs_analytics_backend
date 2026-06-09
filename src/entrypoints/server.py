import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from apps.api import server_router
from apps.ws import server_ws_router
from core.settings import get_settings

settings = get_settings()


def get_application() -> FastAPI:
    """
    Create application instance.

    :return: Application instance
    """
    app = FastAPI(
        title=settings.APP_NAME,
        version="1",
        docs_url=settings.DOCS_URL,
        openapi_url=settings.OPENAPI_URL,
    )
    app.include_router(server_router)
    app.include_router(server_ws_router)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    return app


app = get_application()


if __name__ == "__main__":
    uvicorn.run(
        "server:app",
        host=settings.BACKEND_HOST,
        port=settings.BACKEND_PORT,
        reload=settings.RELOAD,
        workers=settings.BACKEND_WORKERS,
    )
