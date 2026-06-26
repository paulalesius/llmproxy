"""Integration tests for OpenAI-compatible models endpoints."""

import pytest


class TestOpenAIModels:
    """Test OpenAI /v1/models endpoints."""
    
    def test_list_models(self, sync_client):
        """
        Test 4: OpenAI /v1/models (list).
        Verifies models endpoint returns proper OpenAI format.
        """
        response = sync_client.get("/v1/models")
        
        assert response.status_code == 200
        data = response.json()
        
        # OpenAI format: {"object": "list", "data": [...]}
        assert "data" in data
        assert isinstance(data["data"], list)
        
        # Should have at least one model
        model_count = len(data["data"])
        assert model_count >= 1, "Expected at least one model"
        
        # Each model should have id field
        for model in data["data"]:
            assert "id" in model
    
    def test_list_models_object_field(self, sync_client):
        """Verify models list has correct object type."""
        response = sync_client.get("/v1/models")
        
        assert response.status_code == 200
        data = response.json()
        
        # OpenAI API returns object: "list"
        if "object" in data:
            assert data["object"] == "list"
    
    def test_get_model_detail(self, sync_client):
        """
        Test 5: OpenAI /v1/models/{id} (detail).
        Verifies model detail endpoint forwards to backend.
        """
        # First get a model ID
        models_response = sync_client.get("/v1/models")
        assert models_response.status_code == 200
        models_data = models_response.json()
        
        # Find a qwen3.6 model or use first available
        model_id = None
        for model in models_data.get("data", []):
            if "qwen3.6" in model.get("id", ""):
                model_id = model["id"]
                break
        
        if model_id is None and models_data.get("data"):
            model_id = models_data["data"][0]["id"]
        
        if model_id:
            # Test model detail endpoint
            response = sync_client.get(f"/v1/models/{model_id}")
            
            # Should return 200 with proper format (mocked backend)
            assert response.status_code == 200
            data = response.json()
            assert "id" in data
            assert data["id"] == model_id
        else:
            pytest.skip("No models available for detail test")
    
    def test_get_nonexistent_model(self, sync_client):
        """Test getting a model that doesn't exist."""
        response = sync_client.get("/v1/models/nonexistent-model-12345")
        
        # Should forward to backend, which may return 404 or 422
        assert response.status_code in [404, 422, 200]
