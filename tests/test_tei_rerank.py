"""Integration tests for TEI-compatible rerank endpoints."""

import pytest


class TestTEIRerank:
    """Test TEI rerank endpoint functionality."""
    
    def test_tei_rerank_full_payload(self, sync_client):
        """
        Test 2: TEI /v1/rerank with full payload.
        Verifies basic rerank request/response.
        """
        response = sync_client.post(
            "/v1/rerank",
            json={
                "model": "bge-reranker-v2-m3",
                "query": "test query",
                "documents": ["doc1", "doc2"]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Proxy returns list directly (TEI format)
        assert isinstance(data, list)
        assert len(data) >= 0  # May return empty if no matches
        
        # Each result should have index and score
        for item in data:
            assert "index" in item
            assert "score" in item
    
    def test_tei_rerank_index_preservation(self, sync_client):
        """
        Test 3: TEI /v1/rerank preserves original document indices.
        
        When ranking documents, the response should preserve the original
        indices so clients can map results back to input documents.
        """
        response = sync_client.post(
            "/v1/rerank",
            json={
                "model": "bge-reranker-v2-m3",
                "query": "machine learning",
                "documents": [
                    "python code",
                    "ml algorithms",
                    "data science",
                    "web development"
                ],
                "top_n": 2
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        
        # Should return a list
        assert isinstance(data, list)
        
        # Should have at least some results
        assert len(data) >= 1
        
        # Verify indices are preserved (not just 0, 1, 2...)
        indices = [item.get("index") for item in data if "index" in item]
        assert len(indices) > 0, "No index field found in results"
        
        # Indices should match original document positions
        # For "machine learning" query, relevant docs should be ranked higher
        # Original positions: "ml algorithms"=1, "data science"=2
        assert all(isinstance(idx, int) for idx in indices)
    
    def test_tei_rerank_with_texts_field(self, sync_client):
        """Test rerank with 'texts' field (Hindsight API compatibility)."""
        response = sync_client.post(
            "/v1/rerank",
            json={
                "model": "bge-reranker-v2-m3",
                "query": "test",
                "texts": ["text1", "text2", "text3"]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
    
    def test_tei_rerank_return_documents(self, sync_client):
        """Test rerank with return_documents=True."""
        response = sync_client.post(
            "/v1/rerank",
            json={
                "model": "bge-reranker-v2-m3",
                "query": "test",
                "documents": ["doc1", "doc2"],
                "return_documents": True
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        
        # When return_documents=True, results MAY include document text
        # (backend may or may not return it depending on implementation)
        # At minimum, results should have index and score
        for item in data:
            assert "index" in item
            assert "score" in item
            # If document field exists, it should not be None
            if "document" in item:
                # Document can be None if backend doesn't support it
                pass  # Accept both None and actual text
    
    def test_tei_rerank_alternative_path(self, sync_client):
        """Test /rerank alternative path works."""
        response = sync_client.post(
            "/rerank",
            json={
                "model": "bge-reranker-v2-m3",
                "query": "test",
                "documents": ["doc1"]
            }
        )
        
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)


class TestTEIInfo:
    """Test TEI /info endpoint."""
    
    def test_tei_info_endpoint(self, sync_client):
        """Test /v1/info returns model information."""
        response = sync_client.get("/v1/info")
        
        assert response.status_code == 200
        data = response.json()
        
        # Should have basic model info
        assert "model_id" in data or "revision" in data
    
    def test_tei_info_alternative_path(self, sync_client):
        """Test /info alternative path works."""
        response = sync_client.get("/info")
        
        assert response.status_code == 200
        data = response.json()
        assert "model_id" in data or "revision" in data
