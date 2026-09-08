"""Official source adapter registry."""

from .base import Fetcher, available, get_fetcher, register, source_key

from . import amazon  # noqa: F401
from . import ashby  # noqa: F401
from . import gem  # noqa: F401
from . import greenhouse  # noqa: F401
from . import lever  # noqa: F401
from . import rippling  # noqa: F401
from . import smartrecruiters  # noqa: F401
from . import tesla  # noqa: F401
from . import workday  # noqa: F401

__all__ = ["Fetcher", "available", "get_fetcher", "register", "source_key"]
