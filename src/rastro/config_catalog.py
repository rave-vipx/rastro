"""Configuration points that are not job-file keys: Python keyword arguments, operator
grants, environment variables, plus where each part is documented.

Data only: ``capabilities_render`` reads ``SECTIONS`` for the agent
snippet. Guard tests check that every parameter, grant flag and ``RASTRO_*`` name in the
code has an entry here.
"""

from __future__ import annotations

from rastro.config_catalog_access import GRANTS as GRANTS
from rastro.config_catalog_ai import CLI_DOCS as CLI_DOCS
from rastro.config_catalog_ai import ENV_VARS as ENV_VARS
from rastro.config_catalog_crawl import JOB_DOCS as JOB_DOCS
from rastro.config_catalog_crawl import SECTIONS as SECTIONS
from rastro.config_catalog_dataset import PYTHON_MODELS as PYTHON_MODELS
from rastro.config_catalog_dataset import PYTHON_PARAMS as PYTHON_PARAMS
from rastro.config_catalog_studio import CONFIG_RULE as CONFIG_RULE
from rastro.config_catalog_studio import STUDIO_RESULTS_RULE as STUDIO_RESULTS_RULE
