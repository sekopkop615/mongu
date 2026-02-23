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
            raise MonguError("MGU_Paused")
        if amount_wei == 0:
            raise MGU_ZeroAmount()
        if pool_id not in self._pools:
            raise MGU_PoolNotFound()
        p = self._pools[pool_id]
        if p.unlocked:
            raise MGU_PoolStillLocked()
        if p.total_deposited_wei + amount_wei > p.cap_wei:
            raise MGU_CapExceeded()
        key = (pool_id, fren)
        new_total = self._fren_share_wei.get(key, 0) + amount_wei
        if p.max_per_fren_wei != 2**256 - 1 and new_total > p.max_per_fren_wei:
            raise MGU_MaxPerFrenExceeded()
        self._fren_share_wei[key] = new_total
        if fren not in self._pool_frens[pool_id]:
            self._pool_frens[pool_id].append(fren)
            self._fren_pool_ids.setdefault(fren, []).append(pool_id)
        p.total_deposited_wei += amount_wei
        self._total_deposited_across_pools += amount_wei

    def fund_pool(self, pool_id: str, amount_wei: int) -> None:
        if amount_wei == 0:
            raise MGU_ZeroAmount()
        if pool_id not in self._pools:
            raise MGU_PoolNotFound()
        self._pools[pool_id].total_reward_wei += amount_wei
        self._total_reward_across_pools += amount_wei

    def unlock_pool(self, pool_id: str, caller: str) -> None:
        if pool_id not in self._pools:
            raise MGU_PoolNotFound()
        p = self._pools[pool_id]
        if p.unlocked:
            raise MGU_AlreadyUnlocked()
        if self._current_block < p.unlock_block:
            raise MGU_PoolNotUnlocked()
        if caller != p.creator and caller != self.pad_keeper:
            raise MGU_NotPoolCreator()
        p.unlocked = True

    def pending_reward(self, pool_id: str, fren: str) -> int:
        if pool_id not in self._pools:
            return 0
        p = self._pools[pool_id]
        if not p.unlocked:
            return 0
        share = self._fren_share_wei.get((pool_id, fren), 0)
        if share == 0:
            return 0
        total_dep = p.total_deposited_wei
        total_reward = p.total_reward_wei
        reward_share = (total_reward * share) // total_dep if total_dep else 0
        fee = (reward_share * self.protocol_fee_basis_points) // MGU_BASIS_DENOM
        net = reward_share - fee
        already_claimed = self._fren_claimed_wei.get((pool_id, fren), 0)
        return max(0, net - already_claimed)

    def claim(self, pool_id: str, fren: str) -> int:
        if pool_id not in self._pools:
            raise MGU_PoolNotFound()
        p = self._pools[pool_id]
        if not p.unlocked:
            raise MGU_PoolStillLocked()
        share = self._fren_share_wei.get((pool_id, fren), 0)
        if share == 0:
            raise MGU_NoShares()
        total_dep = p.total_deposited_wei
        total_reward = p.total_reward_wei
        reward_share = (total_reward * share) // total_dep if total_dep else 0
        fee = (reward_share * self.protocol_fee_basis_points) // MGU_BASIS_DENOM
        net = reward_share - fee
        key = (pool_id, fren)
        already_claimed = self._fren_claimed_wei.get(key, 0)
        to_send = max(0, net - already_claimed)
        if to_send > 0:
            self._fren_claimed_wei[key] = net
            self._treasury_balance += (reward_share - net)
        return to_send

    def get_pool(self, pool_id: str) -> Optional[PoolInfo]:
        return self._pools.get(pool_id)

    def get_fren_share(self, pool_id: str, fren: str) -> Tuple[int, int]:
        share = self._fren_share_wei.get((pool_id, fren), 0)
        claimed = self._fren_claimed_wei.get((pool_id, fren), 0)
        return (share, claimed)

    def get_pool_fren_count(self, pool_id: str) -> int:
        return len(self._pool_frens.get(pool_id, []))

    def get_pool_ids(self) -> List[str]:
        return list(self._pool_ids)

    def get_fren_pool_ids(self, fren: str) -> List[str]:
        return list(self._fren_pool_ids.get(fren, []))

    def treasury_balance(self) -> int:
        return self._treasury_balance

    def total_deposited_across_pools(self) -> int:
        return self._total_deposited_across_pools

    def total_reward_across_pools(self) -> int:
        return self._total_reward_across_pools

    def set_protocol_fee_basis(self, basis_points: int, caller: str) -> None:
        if caller != self.pad_keeper:
            raise MGU_NotPadKeeper()
        if basis_points > MGU_MAX_FEE_BASIS:
            raise MGU_FeeBasisTooHigh()
        self.protocol_fee_basis_points = basis_points

    def raise_pool_cap(self, pool_id: str, new_cap_wei: int, caller: str) -> None:
        if caller != self.pad_keeper:
            raise MGU_NotPadKeeper()
        if pool_id not in self._pools:
            raise MGU_PoolNotFound()
        p = self._pools[pool_id]
        if new_cap_wei <= p.cap_wei:
            raise MGU_NewCapLower()
        p.cap_wei = new_cap_wei

    def set_pool_max_per_fren(self, pool_id: str, max_per_fren_wei: int, caller: str) -> None:
        if caller != self.pad_keeper:
            raise MGU_NotPadKeeper()
        if pool_id not in self._pools:
            raise MGU_PoolNotFound()
        self._pools[pool_id].max_per_fren_wei = max_per_fren_wei

    def pause(self, caller: str) -> None:
        if caller != self.pad_keeper:
            raise MGU_NotPadKeeper()
        self._paused = True

    def unpause(self, caller: str) -> None:
        if caller != self.pad_keeper:
            raise MGU_NotPadKeeper()
        self._paused = False

    def is_paused(self) -> bool:
        return self._paused


# ---------------------------------------------------------------------------
# MonguLaunchpadMath — mirror of Solidity library
# ---------------------------------------------------------------------------

def mul_div(a: int, b: int, denom: int) -> int:
    if denom == 0:
        return 0
    return (a * b) // denom


def basis_points_of(amount: int, basis_points: int, basis_denom: int = MGU_BASIS_DENOM) -> int:
    return (amount * basis_points) // basis_denom


def share_of(total_reward: int, my_share: int, total_deposited: int) -> int:
    if total_deposited == 0:
        return 0
    return (total_reward * my_share) // total_deposited


def fee_from_reward(reward_wei: int, basis_points: int, basis_denom: int = MGU_BASIS_DENOM) -> int:
    return (reward_wei * basis_points) // basis_denom


def net_after_fee(reward_wei: int, basis_points: int, basis_denom: int = MGU_BASIS_DENOM) -> int:
    fee = (reward_wei * basis_points) // basis_denom
    return reward_wei - fee


def fill_ratio_basis_points(deposited: int, cap: int) -> int:
    if cap == 0:
        return 0
    return (deposited * MGU_BASIS_DENOM) // cap


# ---------------------------------------------------------------------------
# Pool ID and label helpers (keccak256-style for compatibility)
# ---------------------------------------------------------------------------

def pool_id_from_name(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def label_hash_from_string(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Random address and hex generation (for tests / fixtures; never reuse on mainnet)
# ---------------------------------------------------------------------------

def random_hex_bytes(n: int = 32) -> str:
    return secrets.token_hex(n)


def random_address_like() -> str:
    return "0x" + secrets.token_hex(20)


# ---------------------------------------------------------------------------
# Global stats view
# ---------------------------------------------------------------------------

def get_global_stats(engine: MonguLaunchpadEngine) -> Dict[str, Any]:
    return {
        "total_pools": len(engine.get_pool_ids()),
        "total_deposited_wei": engine.total_deposited_across_pools(),
        "total_reward_wei": engine.total_reward_across_pools(),
        "treasury_balance": engine.treasury_balance(),
        "current_block": engine.get_block(),
        "protocol_fee_basis_points": engine.protocol_fee_basis_points,
        "paused": engine.is_paused(),
    }


def get_fren_stats(engine: MonguLaunchpadEngine, fren: str) -> Dict[str, Any]:
    pool_ids = engine.get_fren_pool_ids(fren)
    total_deposited = 0
    total_claimed = 0
    for pid in pool_ids:
        share, claimed = engine.get_fren_share(pid, fren)
        total_deposited += share
        total_claimed += claimed
    total_pending = 0
    for pid in pool_ids:
        total_pending += engine.pending_reward(pid, fren)
    return {
        "pool_count": len(pool_ids),
        "total_deposited_wei": total_deposited,
        "total_claimed_wei": total_claimed,
        "total_pending_wei": total_pending,
    }


# ---------------------------------------------------------------------------
# Batch operations (mirror Solidity batch limits)
# ---------------------------------------------------------------------------

def ape_in_batch(
    engine: MonguLaunchpadEngine,
    pool_id: str,
    frens: List[str],
    amounts_wei: List[int],
) -> None:
    if len(frens) != len(amounts_wei):
        raise MGU_ArrayLengthMismatch()
    if len(frens) > MGU_BATCH_LIMIT:
        raise MGU_BatchTooLarge()
    total_new = sum(amounts_wei)
    p = engine.get_pool(pool_id)
    if not p:
        raise MGU_PoolNotFound()
    if p.total_deposited_wei + total_new > p.cap_wei:
        raise MGU_CapExceeded()
    for i, fren in enumerate(frens):
        if amounts_wei[i] > 0 and fren:
            engine.ape_in(pool_id, fren, amounts_wei[i])


def create_pool_batch(
    engine: MonguLaunchpadEngine,
    pool_ids: List[str],
    label_hashes: List[str],
    cap_wei_arr: List[int],
    unlock_block_arr: List[int],
    caller: str,
) -> None:
    if not (len(pool_ids) == len(label_hashes) == len(cap_wei_arr) == len(unlock_block_arr)):
        raise MGU_ArrayLengthMismatch()
    if len(pool_ids) > MGU_BATCH_LIMIT:
        raise MGU_BatchTooLarge()
    for i in range(len(pool_ids)):
        engine.create_pool(
            pool_ids[i],
            label_hashes[i],
            cap_wei_arr[i],
            unlock_block_arr[i],
            caller,
        )


# ---------------------------------------------------------------------------
# CLI / script entry (optional)
# ---------------------------------------------------------------------------

def main() -> None:
    pad_keeper = "0x" + secrets.token_hex(20)
    treasury = "0x" + secrets.token_hex(20)
    engine = MonguLaunchpadEngine(
        pad_keeper=pad_keeper,
        treasury=treasury,
        deploy_block=1000,
        protocol_fee_basis_points=250,
    )
    engine.set_block(1000)
    pool_id = pool_id_from_name("mongu_fren_pool_1")
    engine.create_pool(pool_id, label_hash_from_string("Mongu Fren Pool"), 10**18, 2000, pad_keeper)
    fren1 = "0x" + secrets.token_hex(20)
    fren2 = "0x" + secrets.token_hex(20)
    engine.ape_in(pool_id, fren1, 5 * 10**17)
    engine.ape_in(pool_id, fren2, 3 * 10**17)
    engine.fund_pool(pool_id, 2 * 10**17)
    engine.set_block(2000)
    engine.unlock_pool(pool_id, pad_keeper)
    c1 = engine.claim(pool_id, fren1)
    c2 = engine.claim(pool_id, fren2)
    print("Mongu launchpad engine run OK.")
    print("Fren1 claimed:", c1, "Fren2 claimed:", c2)
    print("Global stats:", get_global_stats(engine))


# ---------------------------------------------------------------------------
# Serialization / API payloads
# ---------------------------------------------------------------------------

def pool_info_to_dict(p: PoolInfo) -> Dict[str, Any]:
    return {
        "pool_id": p.pool_id,
        "creator": p.creator,
        "label_hash": p.label_hash,
        "cap_wei": p.cap_wei,
        "total_deposited_wei": p.total_deposited_wei,
        "total_reward_wei": p.total_reward_wei,
        "unlock_block": p.unlock_block,
        "created_at_block": p.created_at_block,
        "max_per_fren_wei": p.max_per_fren_wei,
        "unlocked": p.unlocked,
        "exists": p.exists,
        "remaining_cap": p.remaining_cap(),
        "fill_basis_points": p.fill_basis_points(),
        "is_full": p.is_full(),
    }


def engine_pool_list(engine: MonguLaunchpadEngine, offset: int = 0, limit: int = MGU_VIEW_BATCH_MAX) -> List[Dict[str, Any]]:
    ids = engine.get_pool_ids()
    if offset >= len(ids):
        return []
    end = min(offset + limit, len(ids))
