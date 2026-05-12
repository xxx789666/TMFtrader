"""每日交易覆盤的「資料層」。

只負責讀 data/performance/daily/*_live*.json 與算 Python 端統計。
LLM 推論、prompt、排程、推送、記憶等全部交給 Hermes Agent（~/.hermes/）處理。
Hermes skill 透過 `python -m review.tools_for_hermes ...` 取得 JSON 結果。
"""
