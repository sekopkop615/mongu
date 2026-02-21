# mongu_launchpad_engine.py
# Banana split allocation for fren pools — Python engine and simulator.
# Matches MonguLaunchpad.sol logic for off-chain tooling, tests, and AlphaMong backend.
# No promises, just vibes. Use with EVM mainnets at your own risk.

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any

# ---------------------------------------------------------------------------
# Constants (must match Solidity where applicable)
# ---------------------------------------------------------------------------

MGU_BASIS_DENOM = 10_000
MGU_MAX_FEE_BASIS = 400
MGU_MAX_POOLS = 128
MGU_MIN_POOL_CAP_WEI = 10**16  # 0.01 ether
MGU_BATCH_LIMIT = 32
MGU_VIEW_BATCH_MAX = 64
MGU_MAX_VESTING_PHASES = 12
MGU_SCALE = 10**18

MGU_PAD_DOMAIN = hashlib.sha256(b"MonguLaunchpad.MGU_PAD_DOMAIN").hexdigest()
MGU_POOL_NAMESPACE = hashlib.sha256(b"MonguLaunchpad.MGU_POOL_NAMESPACE").hexdigest()
MGU_FREN_ROLE_TAG = hashlib.sha256(b"MonguLaunchpad.MGU_FREN_ROLE_TAG").hexdigest()
MGU_VESTING_TAG = hashlib.sha256(b"MonguLaunchpad.MGU_VESTING_TAG").hexdigest()


class MonguError(Exception):
    """Base error for Mongu engine."""
    pass


class MGU_ZeroPoolId(MonguError):
    pass


class MGU_ZeroAddress(MonguError):
    pass


class MGU_NotPadKeeper(MonguError):
    pass


class MGU_PoolAlreadyExists(MonguError):
    pass


class MGU_PoolNotFound(MonguError):
    pass


class MGU_PoolNotUnlocked(MonguError):
    pass


class MGU_PoolStillLocked(MonguError):
    pass


class MGU_CapExceeded(MonguError):
    pass


class MGU_ZeroAmount(MonguError):
