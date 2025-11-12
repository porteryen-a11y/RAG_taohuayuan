import os
import json
import requests
from dotenv import load_dotenv
from openai import OpenAI

# 載入環境變數
load_dotenv()

# Supabase / OpenAI 設定
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

TABLE_NAME = "taohuayuan_vectors"
EMBEDDING_MODEL = "text-embedding-ada-002"  # 1536 dimensions
CHAT_MODEL = os.getenv("OPENAI_CHAT_MODEL", "gpt-4.1-mini")  # 可依需要調整

if not all([SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY]):
    print("Error: 缺少必要環境變數: SUPABASE_URL / SUPABASE_KEY / OPENAI_API_KEY")
    exit(1)

POSTGREST_RPC_URL = f"{SUPABASE_URL}/rest/v1/rpc"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
}

openai_client = OpenAI(api_key=OPENAI_API_KEY)


def get_query_embedding(query: str) -> list[float]:
    """對使用者問題產生向量嵌入"""
    print("-> 正在生成問題向量...")
    try:
        resp = openai_client.embeddings.create(
            model=EMBEDDING_MODEL,
            input=query,
        )
        return resp.data[0].embedding
    except Exception as e:
        print(f"Error generating embedding: {e}")
        return []


def search_similar_chunks(query_embedding: list[float], match_count: int = 5):
    """
    呼叫 Supabase 的向量相似度查詢函數: match_taohuayuan
    """
    if not query_embedding:
        return []

    rpc_name = "match_taohuayuan"
    url = f"{POSTGREST_RPC_URL}/{rpc_name}"

    payload = {
        "query_embedding": query_embedding,
        "match_count": match_count,
        "match_threshold": 0.0, # 預設不設門檻
    }

    print(f"-> 正在 Supabase 檢索 {match_count} 個相似片段...")
    try:
        res = requests.post(url, headers=HEADERS, data=json.dumps(payload))
        res.raise_for_status() # 對於 4xx/5xx 狀態碼拋出異常
        
        # 預期返回: [{ content, similarity, source, ...}, ...]
        return res.json()
    except requests.exceptions.RequestException as e:
        print(f"Error Supabase RPC 查詢失敗: {e}")
        print(f"Response text: {res.text if 'res' in locals() else 'No response'}")
        return []


def build_answer(question: str, contexts: list[dict]) -> str:
    """使用檢索到的段落作為 context，呼叫 LLM 生成回答"""
    if not contexts:
        return "依現有片段無法確定，因為未能從向量資料庫中檢索到相關內容。"

    context_text = "\n\n---\n\n".join(
        f"[片段 {i+1}] 相似度: {c.get('similarity', 'N/A'):.4f} | 來源: {c.get('source', 'unknown')}\n內容:\n{c.get('content', '')}"
        for i, c in enumerate(contexts)
    )

    system_prompt = (
        "你是一個熟悉〈桃花源記〉的中文助理。"
        "請只根據提供的原文片段回答問題，"
        "使用繁體中文，簡潔、準確，不捏造原文沒有的內容。"
        "若 context 中找不到答案，請明確說明『依現有片段無法確定』。"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": (
                f"使用下列與〈桃花源記〉相關的片段作為依據回答問題。\n\n"
                f"【檢索到的原文片段】\n{context_text}\n\n"
                f"【問題】\n{question}"
            )
        }
    ]

    print(f"-> 正在使用 {CHAT_MODEL} 生成答案...")
    try:
        response = openai_client.chat.completions.create(
            model=CHAT_MODEL,
            messages=messages,
            temperature=0.1,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error generating chat completion: {e}"


def log_qa_to_supabase(question: str, answer: str, contexts: list[dict]) -> None:
    """
    將 Q&A 與檢索使用的 context 儲存到 Supabase qa_logs 資料表，
    以便後續做分析 / 評估 / 微調資料準備。

    建議在 Supabase 建立如下資料表 (可在 README 中說明)：

        create table if not exists qa_logs (
          id bigserial primary key,
          question text,
          answer text,
          contexts jsonb,
          created_at timestamptz default timezone('utc', now())
        );

    並設定適當的 RLS 規則與 API 權限。
    """
    try:
        qa_table_url = f"{SUPABASE_URL}/rest/v1/qa_logs"
        payload = {
            "question": question,
            "answer": answer,
            "contexts": contexts,
        }
        headers = {
            "apikey": SUPABASE_KEY,
            "Authorization": f"Bearer {SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        res = requests.post(qa_table_url, headers=headers, data=json.dumps(payload))
        if not (200 <= res.status_code < 300):
            print(f"[WARN] 儲存 Q&A 至 qa_logs 失敗 (status={res.status_code}): {res.text}")
    except Exception as e:
        print(f"[WARN] 儲存 Q&A 過程發生例外: {e}")


def main():
    """主執行邏輯：互動式 RAG 查詢迴圈，並將 Q&A 紀錄到 Supabase。"""
    print("--- 桃花源記 RAG 查詢系統 ---")
    print(f"使用模型: {CHAT_MODEL} / 向量表: {TABLE_NAME}")
    print("輸入 'exit' 或 'quit' 結束程式。")
    print("-" * 30)

    while True:
        question = input("請輸入您的問題 (關於桃花源記): ").strip()
        
        if question.lower() in ['exit', 'quit']:
            print("程式結束。")
            break
        
        if not question:
            continue

        try:
            # 1. 產生問題向量
            query_embedding = get_query_embedding(question)

            # 2. 檢索相似片段
            contexts = search_similar_chunks(query_embedding, match_count=5)

            # 3. 生成答案
            answer = build_answer(question, contexts)

            # 4. 將本次 Q&A 與使用的 context 紀錄到 Supabase qa_logs
            log_qa_to_supabase(question, answer, contexts)

            # 5. 輸出結果
            print("\n" + "=" * 10 + " 答案 " + "=" * 10)
            print(answer)
            print("=" * 26 + "\n")

        except Exception as e:
            print(f"\n發生未預期的錯誤: {e}\n")


if __name__ == "__main__":
    main()