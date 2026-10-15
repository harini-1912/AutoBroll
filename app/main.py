"""FastAPI Application Entrypoint conforming to Integration Standard #1-#10."""
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.middleware import CorrelationIdMiddleware
from app.api.routes import api_router
from app.api.v1.endpoints.health import router as health_router
from app.core.config import settings
from app.core.database import db
from app.core.exceptions import AppException
from app.core.logging import logger, setup_logging

# Setup structured logging
setup_logging(settings.LOG_LEVEL)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle events: startup and shutdown."""
    logger.info("Starting %s v%s in %s mode", settings.APP_NAME, settings.APP_VERSION, settings.ENVIRONMENT)
    db.init_db()
    settings.ensure_directories()
    yield
    logger.info("Shutting down %s", settings.APP_NAME)

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="IncuBrix Script and Voiceover to B-roll Video Engine API",
    openapi_url="/api/v1/openapi.json",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID Middleware
app.add_middleware(CorrelationIdMiddleware)

# --- Standardized Exception Handlers (Integration Standard #5) ---

@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    logger.warning("Application error [%s]: %s (details: %s)", exc.code, exc.message, exc.details)
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    raw_errors = exc.errors()
    first_msg = raw_errors[0].get("msg") if raw_errors else "Validation failed."
    logger.warning("Validation error on %s: %s", request.url.path, raw_errors)

    clean_errors = []
    for err in raw_errors:
        clean_err = {}
        for k, v in err.items():
            if k == "ctx" and isinstance(v, dict):
                clean_err["ctx"] = {ck: str(cv) for ck, cv in v.items()}
            elif isinstance(v, (str, int, float, bool, type(None))):
                clean_err[k] = v
            elif isinstance(v, (list, tuple)):
                clean_err[k] = [str(item) for item in v]
            else:
                clean_err[k] = str(v)
        clean_errors.append(clean_err)

    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "error": {
                "code": "INVALID_INPUT",
                "message": f"Invalid input: {first_msg}",
                "retryable": False,
                "details": {"validation_errors": clean_errors},
            }
        },
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled server exception on %s: %s", request.url.path, str(exc), exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal error occurred. Please retry later.",
                "retryable": True,
                "details": {"error": str(exc)},
            }
        },
    )

# --- Mount Routers ---

# Root level health checks (Integration Standard #8)
app.include_router(health_router)

# All versioned routes under /api/v1/ (Integration Standard #2)
app.include_router(api_router)

# Serve openapi.yaml directly
@app.get("/openapi.yaml", include_in_schema=False)
def get_openapi_yaml():
    yaml_path = Path("openapi.yaml")
    if yaml_path.exists():
        return FileResponse(yaml_path, media_type="text/yaml")
    return JSONResponse(status_code=404, content={"message": "openapi.yaml not generated yet"})

# Serve Web UI
static_dir = Path("static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root_ui():
    index_file = Path("static/index.html")
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse(
        """
        <!DOCTYPE html>
        <html>
        <head><title>IncuBrix B-Roll Engine</title></head>
        <body style="font-family:sans-serif;padding:2rem;">
            <h1>IncuBrix B-Roll Video Engine</h1>
            <p>API service running. View documentation at <a href="/docs">/docs</a> or <a href="/openapi.yaml">/openapi.yaml</a>.</p>
        </body>
        </html>
        """
    )
