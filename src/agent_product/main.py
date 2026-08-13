import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from agent_product import __version__
from agent_product.api.routes.chat import router as chat_router
from agent_product.api.routes.git import router as git_router
from agent_product.api.routes.health import router as health_router
from agent_product.api.routes.knowledge import router as knowledge_router
from agent_product.api.routes.workspace import router as workspace_router
from agent_product.core.config import Settings, get_settings
from agent_product.core.logging import configure_logging
from agent_product.db.base import Base
from agent_product.db.session import build_engine, build_session_factory
from agent_product.services.agent import build_agent
from agent_product.services.git import GitIntentRegistry
from agent_product.services.knowledge import KnowledgeBase
from agent_product.services.workspace import WorkspaceRegistry

logger = logging.getLogger(__name__)

# Provider SDKs such as DeepSeek read their credentials from the process
# environment. Keep deployment-provided variables authoritative while making
# the local .env file available to those SDKs as well as pydantic-settings.
load_dotenv(override=False)


def create_app(settings: Settings | None = None, model: Any | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    app_settings.prepare_local_directories()

    engine = build_engine(app_settings)
    session_factory = build_session_factory(engine)
    agent = build_agent(app_settings, model=model)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if app_settings.auto_create_tables:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        logger.info("Application started", extra={"environment": app_settings.app_env})
        yield
        await engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.agent = agent
    application.state.knowledge_base = KnowledgeBase(app_settings.knowledge_dir)
    application.state.workspace_registry = WorkspaceRegistry()
    application.state.git_intents = GitIntentRegistry()

    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "X-API-Key",
            "X-Tenant-ID",
            "X-Workspace-ID",
        ],
    )

    @application.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID", str(uuid4()))[:128]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    application.include_router(health_router)
    application.include_router(chat_router)
    application.include_router(git_router)
    application.include_router(knowledge_router)
    application.include_router(workspace_router)

    @application.get("/", include_in_schema=False)
    async def root() -> dict[str, str]:
        return {
            "name": app_settings.app_name,
            "version": __version__,
            "docs": "/docs",
        }

    return application


app = create_app()
