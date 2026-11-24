# Personal Glossary (供 /wait-what 與每日早報背景抽屜對照)

## 規則
- **已熟術語**：讀者已有實戰經驗，解釋新消息時直接沿用，**不要花字數科普基本定義**。
- **類比錨點**：遇到陌生的 AI / 論文 / 系統名詞時，優先拿讀者熟悉的「已熟術語」做一對一類比。

## 已熟術語（無需科普）
- **VLM / Multimodal LLM**：視覺語言模型（實務用於影片理解、Captioning、視覺判斷）。
- **Edge AI / 量化（INT8 / FP8 / QAT / PTQ）**：嵌入式裝置（ARMv7 / IPU / DLA）部署模型與精度壓縮。
- **vLLM / KV-Cache / Prefill & Decode**：高併發 LLM 推論引擎、TTFT 與 TPOT 延遲拆分、開環壓測。
- **Agent Harness / Skills / MCP / Subagent**：多 Agent 派工、工具協議、Context 管理、Fresh-context 驗收。
- **RAG / Embedding / Vector Search**：檢索增強與語意搜尋。
- **Eval / Ground Truth / Precision & Recall / Bootstrap CI**：評測集設計、偏差與顯著性檢定。

## 偏好關注視角（`why_it_matters_to_you` 優先切入點）
1. 對 **推論延遲 / 顯存 / 部署成本** 的實質影響。
2. 對 **日常寫程式與 Agent 自動化工作流** 能不能立刻拿來用。
3. **社群真實體感 vs 官方宣傳** 的落差（實測翻車點、隱藏限制、授權條款）。
