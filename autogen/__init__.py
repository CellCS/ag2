# Copyright (c) 2023 - 2024, Owners of https://github.com/ag2ai
#
# SPDX-License-Identifier: Apache-2.0
#
# Portions derived from  https://github.com/microsoft/autogen are under the MIT License.
# SPDX-License-Identifier: MIT
import logging

from .agentchat import *
from .code_utils import DEFAULT_MODEL, FAST_MODEL
from .exception_utils import *
from .oai import *
from .version import __version__

# Set the root logger.
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ToDo: Instead of importing *, import individual items from the package and update __all__.
__all__ = [
    "DEFAULT_MODEL",
    "FAST_MODEL",
    "__version__",
]
