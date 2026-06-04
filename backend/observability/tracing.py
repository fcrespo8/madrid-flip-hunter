import logging
import os
from functools import lru_cache

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_langfuse():
    """Return a configured Langfuse client, or None if keys are not set.

    Result is cached — one client per process. If LANGFUSE_PUBLIC_KEY or
    LANGFUSE_SECRET_KEY are absent, tracing is silently disabled and all
    callers that guard with `if langfuse:` become no-ops.
    """
    public_key = os.getenv("LANGFUSE_PUBLIC_KEY")
    secret_key = os.getenv("LANGFUSE_SECRET_KEY")

    if not (public_key and secret_key):
        logger.debug("Langfuse keys not set — tracing disabled")
        return None

    try:
        from langfuse import Langfuse

        host = os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com")
        client = Langfuse(public_key=public_key, secret_key=secret_key, host=host, debug=True)
        logger.info("Langfuse tracing enabled (host: %s)", host)
        return client
    except Exception as e:
        logger.warning("Langfuse init failed: %s — tracing disabled", e)
        return None
