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

    def remaining_cap(self) -> int:
        if self.total_deposited_wei >= self.cap_wei:
            return 0
        return self.cap_wei - self.total_deposited_wei

    def fill_basis_points(self) -> int:
        if self.cap_wei == 0:
            return 0
        return (self.total_deposited_wei * MGU_BASIS_DENOM) // self.cap_wei

    def is_full(self) -> bool:
        return self.total_deposited_wei >= self.cap_wei


# ---------------------------------------------------------------------------
# MonguLaunchpadEngine — in-memory simulator
# ---------------------------------------------------------------------------

class MonguLaunchpadEngine:
    """
    In-memory engine that mirrors MonguLaunchpad.sol logic.
    Used for testing, scripting, and AlphaMong backend simulation.
    """

    def __init__(
        self,
        pad_keeper: str,
        treasury: str,
        deploy_block: int = 0,
        protocol_fee_basis_points: int = 250,
    ):
        if not pad_keeper:
            raise MGU_ZeroAddress()
        if not treasury:
            raise MGU_ZeroAddress()
        if protocol_fee_basis_points > MGU_MAX_FEE_BASIS:
            raise MGU_FeeBasisTooHigh()
        self.pad_keeper = pad_keeper
        self.treasury = treasury
        self.deploy_block = deploy_block
        self.protocol_fee_basis_points = protocol_fee_basis_points
        self._current_block = deploy_block
        self._pools: Dict[str, PoolInfo] = {}
        self._pool_ids: List[str] = []
        self._fren_share_wei: Dict[Tuple[str, str], int] = {}
        self._fren_claimed_wei: Dict[Tuple[str, str], int] = {}
        self._pool_frens: Dict[str, List[str]] = {}
        self._fren_pool_ids: Dict[str, List[str]] = {}
        self._treasury_balance = 0
        self._total_deposited_across_pools = 0
        self._total_reward_across_pools = 0
        self._paused = False

    def set_block(self, block: int) -> None:
        self._current_block = block

    def get_block(self) -> int:
        return self._current_block

    def create_pool(
        self,
        pool_id: str,
        label_hash: str,
        cap_wei: int,
        unlock_block: int,
        caller: str,
    ) -> None:
        if caller != self.pad_keeper:
            raise MGU_NotPadKeeper()
        if not pool_id:
            raise MGU_ZeroPoolId()
        if pool_id in self._pools:
            raise MGU_PoolAlreadyExists()
        if len(self._pool_ids) >= MGU_MAX_POOLS:
            raise MGU_MaxPoolsReached()
        if cap_wei < MGU_MIN_POOL_CAP_WEI:
            raise MGU_MinCap()
        if unlock_block <= self._current_block:
            raise MGU_UnlockInPast()
        max_per_fren = 2**256 - 1  # type(uint256).max
        self._pools[pool_id] = PoolInfo(
            pool_id=pool_id,
            creator=caller,
            label_hash=label_hash,
            cap_wei=cap_wei,
            total_deposited_wei=0,
            total_reward_wei=0,
            unlock_block=unlock_block,
            created_at_block=self._current_block,
            max_per_fren_wei=max_per_fren,
            unlocked=False,
            exists=True,
        )
        self._pool_ids.append(pool_id)
        self._pool_frens[pool_id] = []

    def ape_in(self, pool_id: str, fren: str, amount_wei: int) -> None:
        if self._paused:
