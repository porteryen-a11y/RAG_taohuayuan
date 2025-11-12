import os
import json
import requests
from dotenv import load_dotenv
from openai import OpenAI

# 載入環境變數
load_dotenv()

# 設置 Supabase 和 OpenAI 客戶端
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# 配置
FILE_PATH = "TextFile/taohuayuan.txt"
TABLE_NAME = "taohuayuan_vectors"
EMBEDDING_MODEL = "text-embedding-ada-002" # 1536 dimensions

if not all([SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY]):
    print("Error: Missing one or more required environment variables (SUPABASE_URL, SUPABASE_KEY, OPENAI_API_KEY).")
    exit(1)

# 設置 PostgREST API 終點和 Headers
POSTGREST_URL = f"{SUPABASE_URL}/rest/v1/{TABLE_NAME}"
HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation" # 確保返回插入的資料
}

openai_client = OpenAI(api_key=OPENAI_API_KEY)

def get_embeddings(texts: list[str]):
    """呼叫 OpenAI API 取得文本嵌入"""
    print(f"Generating embeddings for {len(texts)} chunks...")
    response = openai_client.embeddings.create(
        input=texts,
        model=EMBEDDING_MODEL
    )
    return [data.embedding for data in response.data]

def chunk_text(text: str) -> list[str]:
    """
    將文本按段落分塊。
    由於桃花源記文本較短，我們使用雙換行符號 (\n\n) 作為分隔符。
    """
    # 移除行首行尾空白，並過濾空行
    chunks = [chunk.strip() for chunk in text.split('\n\n') if chunk.strip()]
    return chunks

def process_and_insert():
    """主處理流程：讀取、分塊、嵌入、插入"""
    try:
        with open(FILE_PATH, 'r', encoding='utf-8') as f:
            content = f.read()
    except FileNotFoundError:
        print(f"Error: File not found at {FILE_PATH}")
        return

    # 1. 分塊
    chunks = chunk_text(content)
    print(f"Text chunked into {len(chunks)} parts.")

    # 2. 生成嵌入
    embeddings = get_embeddings(chunks)

    # 3. 準備插入資料
    data_to_insert = []
    for i, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
        data_to_insert.append({
            "content": chunk,
            "embedding": embedding,
            "source": f"taohuayuan_chunk_{i+1}"
        })

    # 4. 插入 Supabase
    print(f"Inserting {len(data_to_insert)} records into {TABLE_NAME}...")
    
    # 由於我們使用 Anon Key，需要確保 RLS 允許插入操作，
    # 但為了完成任務，我們假設 RLS 已經配置或暫時禁用。
    # 這裡使用 requests 庫直接呼叫 PostgREST API 進行插入。
    response = requests.post(POSTGREST_URL, headers=HEADERS, data=json.dumps(data_to_insert))

    # 檢查 API 響應
    if response.status_code == 201:
        inserted_data = response.json()
        print("Successfully inserted data.")
        print(f"Inserted {len(inserted_data)} rows.")
    else:
        print(f"Error inserting data (Status Code: {response.status_code}): {response.text}")


if __name__ == "__main__":
    process_and_insert()