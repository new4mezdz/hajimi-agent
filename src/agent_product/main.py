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
from agent_product.api.routes.events import router as events_router
from agent_product.api.routes.git import router as git_router
from agent_product.api.routes.health import router as health_router
from agent_product.api.routes.knowledge import router as knowledge_router
from agent_product.api.routes.models import router as models_router
from agent_product.api.routes.profiles import router as profiles_router
from agent_product.api.routes.settings import router as settings_router
from agent_product.api.routes.skills import router as skills_router
from agent_product.api.routes.support import router as support_router
from agent_product.api.routes.workspace import router as workspace_router
from agent_product.core.config import Settings, get_settings
from agent_product.core.logging import configure_logging
from agent_product.db.base import Base
from agent_product.db.commerce_migrations import migrate_sqlite_commerce_demo_schema
from agent_product.db.session import build_engine, build_session_factory
from agent_product.services.agent_profiles import build_profile_registry
from agent_product.services.agent_runtime import AgentRuntime
from agent_product.services.git import GitIntentRegistry
from agent_product.services.knowledge import KnowledgeBase
from agent_product.services.knowledge_index import create_knowledge_index
from agent_product.services.local_settings import LocalSettingsError, LocalSettingsStore
from agent_product.services.skills import LocalSkillRegistry
from agent_product.services.support import SupportService, seed_support_demo_data
from agent_product.services.workspace import WorkspaceRegistry

logger = logging.getLogger(__name__)

# Provider SDKs such as DeepSeek read their credentials from the process
# environment. Keep deployment-provided variables authoritative while making
# the local .env file available to those SDKs as well as pydantic-settings.
load_dotenv(override=False)


def create_app(
    settings: Settings | None = None,
    model: Any | None = None,
    local_settings_store: LocalSettingsStore | None = None,
) -> FastAPI:
    base_settings = settings or get_settings()
    app_settings = base_settings
    settings_store = local_settings_store
    if settings is None and settings_store is None and base_settings.app_env == "development":
        settings_store = LocalSettingsStore()
    if settings_store is not None:
        try:
            app_settings = settings_store.apply(app_settings)
        except LocalSettingsError:
            logger.exception("Could not restore encrypted local Agent settings")
    configure_logging(app_settings.log_level)
    app_settings.prepare_local_directories()

    engine = build_engine(app_settings)
    session_factory = build_session_factory(engine)
    profiles = build_profile_registry(app_settings)
    agent_runtime = AgentRuntime(app_settings, profiles, model=model)
    knowledge_index = create_knowledge_index(
        app_settings.knowledge_index_backend,
        sqlite_path=app_settings.knowledge_index_path,
    )
    knowledge_base = KnowledgeBase(app_settings.knowledge_dir, index=knowledge_index)
    skill_registry = LocalSkillRegistry(
        app_settings.skills_dir,
        max_bytes=app_settings.skill_max_bytes,
        description_max_length=app_settings.skill_catalog_description_max_length,
    )
    support_service = SupportService(session_factory) if app_settings.support_enabled else None

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if app_settings.auto_create_tables:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            await migrate_sqlite_commerce_demo_schema(
                engine,
                tenant_id=app_settings.support_demo_tenant_id,
                customer_id=app_settings.support_demo_customer_id,
            )
        if app_settings.support_enabled and app_settings.support_demo_seed_enabled:
            await seed_support_demo_data(
                session_factory,
                app_settings.support_demo_tenant_id,
                app_settings.support_demo_customer_id,
            )
        logger.info("Application started", extra={"environment": app_settings.app_env})
        yield
        close_index = getattr(knowledge_index, "close", None)
        if close_index is not None:
            close_index()
        await engine.dispose()

    application = FastAPI(
        title=app_settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    application.state.settings = app_settings
    application.state.base_settings = base_settings
    application.state.local_settings_store = settings_store
    application.state.model_override = model
    application.state.engine = engine
    application.state.session_factory = session_factory
    application.state.agent_runtime = agent_runtime
    # Backwards-compatible default for integrations that have not adopted Profiles yet.
    application.state.agent = agent_runtime.default.agent
    application.state.knowledge_base = knowledge_base
    application.state.skill_registry = skill_registry
    application.state.support_service = support_service
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
            "X-Customer-ID",
            "X-Workspace-ID",
            "X-Agent-Profile",
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
    application.include_router(events_router)
    application.include_router(git_router)
    application.include_router(knowledge_router)
    application.include_router(models_router)
    application.include_router(profiles_router)
    application.include_router(settings_router)
    application.include_router(skills_router)
    application.include_router(support_router)
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
