import logging
import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Module-level token provider for Azure OpenAI (reused across calls)
_token_provider = None


def get_azure_openai_token_provider():
    """Get a cached token provider for Azure OpenAI using DefaultAzureCredential."""
    global _token_provider
    if _token_provider is None:
        credential = DefaultAzureCredential()
        _token_provider = get_bearer_token_provider(credential, "https://cognitiveservices.azure.com/.default")
    return _token_provider


def get_config(cosmos_config_container=None):
    """
    Get configuration from environment variables only.

    Note: cosmos_config_container parameter is kept for backwards compatibility
    but is ignored. Configuration is now sourced exclusively from environment variables.
    """
    load_dotenv()

    # Configuration from environment variables only
    config = {
        "doc_intelligence_endpoint": os.getenv("DOCUMENT_INTELLIGENCE_ENDPOINT", None),
        "mistral_doc_ai_endpoint": os.getenv("MISTRAL_DOC_AI_ENDPOINT", None),
        "mistral_doc_ai_key": os.getenv("MISTRAL_DOC_AI_KEY", None),
        "mistral_doc_ai_model": os.getenv("MISTRAL_DOC_AI_MODEL", "mistral-document-ai-2505"),
        "openai_api_endpoint": os.getenv("AZURE_OPENAI_ENDPOINT", None),
        "openai_api_version": "2024-12-01-preview",
        "openai_model_deployment": os.getenv("AZURE_OPENAI_MODEL_DEPLOYMENT_NAME", None),
        # Content Understanding (selectable full-analyzer extraction backend)
        "content_understanding_endpoint": os.getenv("AZURE_CONTENT_UNDERSTANDING_ENDPOINT", None),
        "content_understanding_api_version": os.getenv("CONTENT_UNDERSTANDING_API_VERSION", "2025-11-01"),
        # Solution-wide default extraction backend ("gpt" | "content_understanding").
        # Overridable per-dataset via processing_options.extraction_backend.
        "extraction_backend": os.getenv("EXTRACTION_BACKEND", "gpt").lower(),
        # Optional cheaper model deployment for the (text-only) summary stage.
        # Falls back to the main deployment when unset.
        "summary_model_deployment": os.getenv("SUMMARY_MODEL_DEPLOYMENT_NAME", None),
        "temp_images_outdir": os.getenv("TEMP_IMAGES_OUTDIR", "/tmp/"),
        "azure_openai_token_provider": get_azure_openai_token_provider(),
    }

    # Log which values are configured (without exposing secrets)
    logger.info("Using OpenAI configuration from environment variables (managed identity auth)")
    logger.info(f"OpenAI endpoint: {'✓ Set' if config['openai_api_endpoint'] else '✗ Missing'}")
    logger.info(f"OpenAI deployment: {'✓ Set' if config['openai_model_deployment'] else '✗ Missing'}")

    return config
