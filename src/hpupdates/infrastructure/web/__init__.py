"""Web subpackage — HpsaCordovaProxy web layer."""

# Import everything from _impl, including private names
from hpupdates.infrastructure.web._impl import *  # noqa: F401,F403
from hpupdates.infrastructure.web._impl import (  # noqa: F401
    CHECK_FOR_UPDATES_MAPPING,
    _basic_object,
    _is_local_device,
)
