"""Let durable workers release their slot while the provider reads a document."""
from copy import copy


PENDING_RECHECK_SECONDS = 2


def handoff_settings(settings):
    # Each accepted provider job is already checkpointed. Poll once, then let the
    # queue inspect/send another document instead of holding a slot for a minute.
    # Do not change the shared settings object used by other worker threads.
    value = copy(settings)
    value.ocr_provider_wait_seconds = 0
    return value
