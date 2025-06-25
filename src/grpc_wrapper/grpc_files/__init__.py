# SPDX-FileCopyrightText: 2025 DENUVO GmbH
# SPDX-License-Identifier: GPL-3.0

import os
import sys

"""
NOTE: this is needed to make the grpc generated files work out of the box, as
they use `import communication_pb2 as communication__pb2` and this need the
communication_pb2.py file to be in the same folder as the grpc_files folder and
therefore the path needs to be sys path.
"""

sys.path.append(os.path.dirname(__file__))
