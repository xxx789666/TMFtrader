"""三支合併 live 單池 position-lock regression。

2026-06-02 上線前置:三支(breakout_v7/day_orb/night_v3)共用一個 TMF live 帳戶,靠
single-pool position-lock 做「同時只 1 支持倉」互斥 + engine 啟動同步「只領養本 owner 的倉」。
驗證兩個安全性質:
  ① live 共用鎖「非我即擋」→ 任一 owner 持倉,其餘 owner 進場被擋(避免同帳戶淨倉合併)。
  ② _foreign_position_holder 的決策:鎖 holder 是別支→視為 foreign(同步時跳過不領養);
     自己或無主→可領養(單策略 live 行為不變)。
"""
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core import position_lock


def _fresh_lock():
    """把 live 鎖檔導到獨立 tmp,避免動到真實 data/active_position.json。"""
    tmp = tempfile.mkdtemp()
    position_lock.LIVE_LOCK_FILE = Path(tmp) / "active_position.json"
    return position_lock.LIVE_LOCK_FILE


def test_single_pool_mutual_exclusion_across_3_owners():
    _fresh_lock()
    # v7 先進場、上鎖
    position_lock.acquire(owner="breakout_v7", side="long", entry_price=46000.0,
                          instrument="TMF", quantity=1, mode="live")
    assert position_lock.get_holder("live") == "breakout_v7"
    # 其餘兩支被擋(非我即擋)
    assert position_lock.is_blocked("day_orb", "live") is not None
    assert position_lock.is_blocked("night_v3", "live") is not None
    # 自己不被自己擋
    assert position_lock.is_blocked("breakout_v7", "live") is None
    # 釋放後全部解除
    position_lock.release("breakout_v7", "live")
    assert position_lock.get_holder("live") is None
    assert position_lock.is_blocked("day_orb", "live") is None
    assert position_lock.is_blocked("night_v3", "live") is None


def test_release_only_by_holder():
    _fresh_lock()
    position_lock.acquire(owner="day_orb", side="short", entry_price=46000.0,
                          instrument="TMF", mode="live")
    # 別支不能誤刪對方的鎖
    position_lock.release("breakout_v7", "live")
    assert position_lock.get_holder("live") == "day_orb"
    position_lock.release("day_orb", "live")
    assert position_lock.get_holder("live") is None


def _foreign(holder, my_owner):
    """複刻 engine._foreign_position_holder 的決策(holder 來自 get_holder)。"""
    return holder if (holder is not None and holder != my_owner) else None


def test_owner_aware_sync_decision():
    _fresh_lock()
    # 別支(v7)持倉 → 對 day_orb 而言是 foreign、同步時應跳過不領養
    position_lock.acquire(owner="breakout_v7", side="long", entry_price=46000.0,
                          instrument="TMF", mode="live")
    holder = position_lock.get_holder("live")
    assert _foreign(holder, "day_orb") == "breakout_v7"     # foreign → skip
    assert _foreign(holder, "night_v3") == "breakout_v7"    # foreign → skip
    assert _foreign(holder, "breakout_v7") is None          # 自己 → 領養
    # 無主孤兒(無鎖)→ 可領養(單策略 live / orphan 行為不變)
    position_lock.release("breakout_v7", "live")
    assert _foreign(position_lock.get_holder("live"), "day_orb") is None


def test_stale_lock_auto_unlink():
    _fresh_lock()
    position_lock.acquire(owner="night_v3", side="long", entry_price=46000.0,
                          instrument="TMF", mode="live")
    # 手動把 entry_unix 改成 13h 前 → 超過 STALE_HOURS(12) 應自動 unlink
    import json
    f = position_lock.LIVE_LOCK_FILE
    d = json.loads(f.read_text(encoding="utf-8"))
    d["entry_unix"] = d["entry_unix"] - 13 * 3600
    f.write_text(json.dumps(d), encoding="utf-8")
    assert position_lock.get_holder("live") is None         # stale → 視為無主
    assert position_lock.is_blocked("day_orb", "live") is None
