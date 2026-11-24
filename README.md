# AI 晨間特刊（Daily AI Editorial Digest）

手機優先（Pixel 10 Pro 單手滑卡）＋ 桌面全景四象限的每日 AI 科技情報管線。  
整合 `last30days-skill`（v4.6.0）、`Apify`、`ig-human` 去 AI 腔規則、`wait-what`（繁中 ASD-STE100）與 `diagram-design`（v2.6）編輯級 SVG。

## 每日自動流程（GitHub Actions 每日台灣時間 07:30）

1. 抓取跨社群 AI 討論：Reddit、HN、GitHub、arXiv、Techmeme ＋（填 Key 後自動解鎖）IG（含留言）、Threads、X（Apify）、選配小紅書。
2. 跨平台同事件聚類 ＋ `data/seen.json` 14 天跨日去重。
3. **雙 LLM 自動備援**：支援 `GEMINI_API_KEY` 與 `ANTHROPIC_API_KEY`，`LLM_PROVIDER=auto` 會依 `config/topics.yaml` 順序呼叫，遇未設定、429 額度用盡或 5xx 自動切換另一家。
4. 生成手機優先一頁式特刊（`docs/index.html` ＋ `docs/archive/YYYY-MM-DD.html`），發佈到 GitHub Pages。
5. 雙重推播：**`ntfy`**（點通知直開網頁）＋ **Gmail**（`lee52025202@gmail.com`）。

## 設定 GitHub Secrets（Settings → Secrets and variables → Actions）

| Secret | 必填 | 用途與免費額度 |
|---|---|---|
| `GEMINI_API_KEY` | 擇一 | Google AI Studio 建「未綁信用卡新專案」即可享 Free Tier |
| `ANTHROPIC_API_KEY` | 擇一 | Claude API Key；與 Gemini 同時設定時自動備援 |
| `SCRAPECREATORS_API_KEY` | 建議 | 10,000 次免費額度；一次解鎖 IG（含留言）＋ Threads ＋ Reddit 補抓 |
| `APIFY_API_TOKEN` | 建議 | 每月 \$5 免費額度；安全抓 X / Twitter，不碰私人 cookie |
| `NTFY_TOPIC` | 建議 | 自訂私密字串（例如 `daily-ai-hucheng-9x2k`），於手機 ntfy App 訂閱同名 topic |
| `GMAIL_APP_PASSWORD` | 建議 | Google 帳號「兩步驟驗證 → 應用程式密碼」16 碼，用於雲端寄信至 `lee52025202@gmail.com` |

## 本機指令

```bash
# 複製範本並填入你手邊有的 key（.env 已 gitignore，絕不進 repo）
cp .env.example .env

# 執行完整每日管線（不發通知可加 --skip-notify）
bash scripts/run_daily.sh --skip-notify

# 指定只用 Claude 或只用 Gemini
bash scripts/run_daily.sh --provider claude --skip-notify
bash scripts/run_daily.sh --provider gemini --skip-notify

# 只抓原始社群資料（零 LLM key 也可測抓取）
bash scripts/run_daily.sh --fetch-only data/tmp/raw.json

# 跑單元測試
uv run --python 3.12 --extra dev pytest -v
```

## 自訂主題與術語表

- 修改 `config/topics.yaml` 即可增刪主題、調整 `importance_weight` / `popularity_weight`、排除關鍵字或開啟 `xiaohongshu: true`。
- 修改 `config/GLOSSARY.md` 可更新你已熟悉的技術術語，`? Wait, what?` 抽屜會自動跳過這些基礎科普。
