"""Web subpackage — HpsaCordovaProxy web layer."""

# Import everything from _impl, including private names
from hpupdates.infrastructure.web._impl import *  # noqa: F401,F403
from hpupdates.infrastructure.web._impl import (  # noqa: F401
    _basic_object,
    _is_local_device,
    CHECK_FOR_UPDATES_MAPPING,
)
