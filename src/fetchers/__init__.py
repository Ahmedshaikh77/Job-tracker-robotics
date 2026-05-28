"""Fetcher registry - importing this triggers registration of all fetchers."""
from .base import get_fetcher, available, register, Fetcher  # noqa: F401

# Import all fetcher modules so their @register decorators run
from . import greenhouse  # noqa: F401
from . import lever  # noqa: F401
from . import ashby  # noqa: F401
from . import workday  # noqa: F401
from . import smartrecruiters  # noqa: F401
from . import oracle_cloud  # noqa: F401
from . import custom  # noqa: F401
