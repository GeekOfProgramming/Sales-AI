from __future__ import annotations
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
import chromadb
from chromadb.api.types import EmbeddingFunction, Documents, Embeddings
import ollama


class OllamaEmbeddingFunction(EmbeddingFunction[Documents]):
    """Custom ChromaDB Embedding Function that invokes local Ollama nomic-embed-text."""

    def __init__(self, model_name: str = "nomic-embed-text", host: Optional[str] = None):
        self.model_name = model_name
        self.host = host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
        self._client = ollama.Client(host=self.host)

    @staticmethod
    def name() -> str:
        """Return the unique name for this embedding function."""
        return "ollama_embedding"

    def get_config(self) -> Dict[str, Any]:
        """Return configuration dictionary for ChromaDB serialization."""
        return {"model_name": self.model_name, "host": self.host}

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "OllamaEmbeddingFunction":
        """Recreate embedding function from saved configuration."""
        return OllamaEmbeddingFunction(**config)

    def __call__(self, input: Documents) -> Embeddings:
        embeddings: Embeddings = []
        for text in input:
            # Safely cap prompt length to avoid exceeding embedding model context limit
            safe_text = text[:3000] if len(text) > 3000 else text
            res = self._client.embeddings(model=self.model_name, prompt=safe_text)
            embeddings.append(res["embedding"])
        return embeddings


class BIMRAGRetriever:
    """Local vector database retriever for BIM standards, ISO 19650, and Revit API rules."""

    def __init__(
        self,
        persist_dir: str = "chroma_db",
        collection_name: str = "bim_rules",
        embedding_model: str = "nomic-embed-text",
        host: Optional[str] = None,
    ):
        self.persist_dir = persist_dir
        self.collection_name = collection_name
        self.embedding_model = embedding_model
        self.host = host or os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")

        # Initialize ChromaDB persistent storage
        self._chroma_client = chromadb.PersistentClient(path=self.persist_dir)
        self._embedding_fn = OllamaEmbeddingFunction(
            model_name=self.embedding_model,
            host=self.host,
        )
        self.collection = self._chroma_client.get_or_create_collection(
            name=self.collection_name,
            embedding_function=self._embedding_fn,
            metadata={"hnsw:space": "cosine"},
        )

    @staticmethod
    def _subchunk_text(text: str, max_chars: int = 1800, overlap: int = 200) -> List[str]:
        """Subdivide long sections into safe chunks respecting embedding window."""
        if len(text) <= max_chars:
            return [text]
        subchunks = []
        start = 0
        while start < len(text):
            end = start + max_chars
            subchunks.append(text[start:end].strip())
            start += max_chars - overlap
        return [s for s in subchunks if s]

    @classmethod
    def chunk_markdown(cls, content: str, source_name: str) -> List[Dict[str, Any]]:
        """Split markdown content into semantic sections based on headers and safe size limits."""
        raw_sections: List[Dict[str, Any]] = []
        lines = content.splitlines()

        current_header = "General"
        current_lines: List[str] = []

        for line in lines:
            if re.match(r"^#{1,3}\s+", line):
                body = "\n".join(current_lines).strip()
                if body:
                    raw_sections.append({"header": current_header, "content": body})
                current_header = re.sub(r"^#{1,3}\s+", "", line).strip()
                current_lines = [line]
            else:
                current_lines.append(line)

        body = "\n".join(current_lines).strip()
        if body:
            raw_sections.append({"header": current_header, "content": body})

        # Apply sub-chunking to ensure no chunk exceeds embedding limits
        chunks: List[Dict[str, Any]] = []
        for sec in raw_sections:
            parts = cls._subchunk_text(sec["content"], max_chars=1800, overlap=200)
            for part in parts:
                chunks.append({
                    "header": sec["header"],
                    "content": part,
                    "source": source_name,
                })

        return chunks

    def index_markdown_file(self, file_path: Union[str, Path]) -> int:
        """Read and index a single markdown file into ChromaDB."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"File not found: {path}")

        content = path.read_text(encoding="utf-8")
        chunks = self.chunk_markdown(content, source_name=path.name)

        if not chunks:
            return 0

        ids = [f"{path.stem}_{i}" for i in range(len(chunks))]
        documents = [c["content"] for c in chunks]
        metadatas = [{"source": c["source"], "header": c["header"]} for c in chunks]

        # Upsert into ChromaDB
        self.collection.upsert(
            ids=ids,
            documents=documents,
            metadatas=metadatas,
        )
        return len(chunks)

    def index_file(self, file_path: Union[str, Path]) -> int:
        """Alias for index_markdown_file to support universal file indexing."""
        return self.index_markdown_file(file_path)

    def delete_source(self, source_name: str) -> int:
        """Remove all chunks associated with a specific file/source from ChromaDB."""
        try:
            existing = self.collection.get(where={"source": source_name})
            if existing and existing.get("ids"):
                ids_to_del = existing["ids"]
                self.collection.delete(ids=ids_to_del)
                return len(ids_to_del)
        except Exception:
            pass
        return 0

    def index_directory(self, directory_path: Union[str, Path]) -> int:
        """Index all markdown files found in the given directory."""
        dir_path = Path(directory_path)
        if not dir_path.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")

        total_chunks = 0
        md_files = list(dir_path.glob("*.md"))
        for md_file in md_files:
            count = self.index_markdown_file(md_file)
            total_chunks += count

        return total_chunks

    def query(self, query_text: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """Perform semantic similarity search for a given question or prompt."""
        if self.collection.count() == 0:
            return []

        results = self.collection.query(
            query_texts=[query_text],
            n_results=min(top_k, self.collection.count()),
        )

        formatted_results: List[Dict[str, Any]] = []
        if results["documents"] and len(results["documents"][0]) > 0:
            for i in range(len(results["documents"][0])):
                doc = results["documents"][0][i]
                meta = results["metadatas"][0][i] if results["metadatas"] else {}
                dist = results["distances"][0][i] if results["distances"] else 0.0
                formatted_results.append({
                    "content": doc,
                    "metadata": meta,
                    "distance": dist,
                    "similarity": round(1.0 - dist, 4) if dist is not None else 1.0,
                })

        return formatted_results

    def get_formatted_context(self, query_text: str, top_k: int = 3) -> str:
        """Return retrieved rules concatenated as a formatted context string for prompts."""
        hits = self.query(query_text=query_text, top_k=top_k)
        if not hits:
            return ""

        context_parts = []
        for i, hit in enumerate(hits, start=1):
            source = hit["metadata"].get("source", "Unknown")
            header = hit["metadata"].get("header", "")
            context_parts.append(
                f"--- Rule Context #{i} [Source: {source} | Topic: {header}] ---\n{hit['content']}"
            )

        return "\n\n".join(context_parts)

    def count(self) -> int:
        """Return the total number of indexed document chunks."""
        return self.collection.count()
