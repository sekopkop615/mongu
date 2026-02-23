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
    pass


class MGU_FeeBasisTooHigh(MonguError):
    pass


class MGU_NoShares(MonguError):
    pass


class MGU_ArrayLengthMismatch(MonguError):
    pass


class MGU_BatchTooLarge(MonguError):
    pass


class MGU_NewCapLower(MonguError):
    pass


class MGU_UnlockInPast(MonguError):
    pass


class MGU_MaxPoolsReached(MonguError):
    pass


class MGU_MinCap(MonguError):
    pass


class MGU_AlreadyUnlocked(MonguError):
    pass


class MGU_NotPoolCreator(MonguError):
    pass


class MGU_MaxPerFrenExceeded(MonguError):
    pass


# ---------------------------------------------------------------------------
# Vesting phase
# ---------------------------------------------------------------------------

@dataclass
class VestingPhase:
    start_block: int
    end_block: int
    basis_points: int


# ---------------------------------------------------------------------------
# Pool info
# ---------------------------------------------------------------------------

@dataclass
class PoolInfo:
    pool_id: str
    creator: str
    label_hash: str
    cap_wei: int
    total_deposited_wei: int
    total_reward_wei: int
    unlock_block: int
    created_at_block: int
    max_per_fren_wei: int
    unlocked: bool
    exists: bool
    vesting_phases: List[VestingPhase] = field(default_factory=list)

