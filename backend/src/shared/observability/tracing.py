"""
OpenTelemetry Tracing setup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from src.shared.config.settings import get_settings
from src.shared.logging.setup import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)


def setup_tracing(app: FastAPI) -> None:
    settings = get_settings()

    if not settings.otel_enabled:
        return

    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.app_version,
            "deployment.environment": settings.app_env.value,
        }
    )

    provider = TracerProvider(resource=resource)

    # Configure OTLP Exporter (usually points to Jaeger or OpenTelemetry Collector)
    exporter = OTLPSpanExporter(
        endpoint=settings.otel_exporter_otlp_endpoint,
        insecure=True,
    )

    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)
    logger.info("opentelemetry_tracing_enabled", endpoint=settings.otel_exporter_otlp_endpoint)


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
