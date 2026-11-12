# iac-classes

抓取教育部產學攜手合作計畫資訊網「數據庫查詢」的專班資料（科大、合作技高、專班名稱、開班年度、產業類別、系所、學制），以及各專班詳細頁的計畫說明。

- `data/classes.csv`：專班清單（以 uuid 去重；`first_seen`/`last_seen` 記錄出現期間，可累積歷年資料）
- `data/details/`：各專班詳細頁（原始 HTML 與純文字）
- `data/run_log.csv`：每次執行紀錄

執行：Actions → scrape → Run workflow，mode 選 `probe`（測試）、`list`、`details` 或 `all`。每週也會自動跑一次。
