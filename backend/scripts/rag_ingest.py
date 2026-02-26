import sys
import os
import asyncio
import logging
from typing import List, Dict, Any, Optional
from openai import OpenAI

# Add backend to path to import src
current_dir = os.path.dirname(os.path.abspath(__file__))
backend_path = os.path.abspath(os.path.join(current_dir, ".."))
if backend_path not in sys.path:
    sys.path.insert(0, backend_path)

from src.lib.supabase_client import supabase_insert, supabase_select

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class RAGIngestor:
    """
    Ingests Markdown documents from /rag_docs into Supabase rag_chunks.
    Handles chunking and embedding.
    """

    def __init__(self):
        self.ai_client = OpenAI(
            api_key=os.getenv("MISTRAL_API_KEY"),
            base_url=os.getenv("MISTRAL_API_BASE_URL", "https://api.mistral.ai/v1")
        )
        self.embedding_model = os.getenv("EMBEDDING_MODEL", "mistral-embed")
        self.docs_dir = os.path.join("backend", "rag_docs")

    async def run_ingestion(self):
        """Main loop to process all files in rag_docs."""
        if not os.path.exists(self.docs_dir):
            logger.error(f"Directory {self.docs_dir} not found.")
            return

        files = [f for f in os.listdir(self.docs_dir) if f.endswith(".md")]
        logger.info(f"Found {len(files)} docs to ingest.")

        for filename in files:
            await self.process_file(filename)

    async def process_file(self, filename: str):
        """Read, chunk, embed, and store a single markdown file."""
        filepath = os.path.join(self.docs_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Simple Chunking (300-600 tokens approx = 1200-2400 chars)
        # For this demo, we'll split by double newline as a proxy for sections
        chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 50]
        
        doc_type = filename.replace(".md", "")
        
        logger.info(f"Processing {filename} ({len(chunks)} chunks)...")

        for i, chunk_text in enumerate(chunks):
            embedding = await self._get_embedding(chunk_text)
            if not embedding:
                continue

            payload = {
                "content": chunk_text,
                "embedding": embedding,
                "metadata": {
                    "source": filename,
                    "type": doc_type,
                    "chunk": i
                }
            }

            try:
                supabase_insert("rag_chunks", payload)
                logger.info(f"  ✓ Chunk {i} stored.")
            except Exception as e:
                logger.error(f"  ❌ Error storing chunk {i}: {e}")

    async def _get_embedding(self, text: str) -> Optional[List[float]]:
        """Mistral Embedding call."""
        try:
            response = self.ai_client.embeddings.create(
                input=[text],
                model=self.embedding_model
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return None

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    
    ingestor = RAGIngestor()
    asyncio.run(ingestor.run_ingestion())
