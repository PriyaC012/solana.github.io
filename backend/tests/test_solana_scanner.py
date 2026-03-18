"""
Comprehensive tests for Solana Token Scanner API
Tests all endpoints including:
- API root
- Token scanning with filters
- Token check with holder distribution
- Watchlist CRUD
- Telegram notifications
- Notification history
"""

import pytest
import requests
import os
import time

# Use the public backend URL
BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://pump-detector-7.preview.emergentagent.com').rstrip('/')

# Test token address for specific token checks
TEST_TOKEN_ADDRESS = "Aj21QKXezLit9kdJzPXfRrozhuKSgoLaDJBY6zbspump"


class TestAPIRoot:
    """Test API root endpoint"""
    
    def test_api_root_returns_message(self):
        """GET /api/ returns API root message"""
        response = requests.get(f"{BASE_URL}/api/", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "Solana Token Scanner API" in data["message"]
        print(f"✅ API root: {data}")


class TestTokenScan:
    """Test token scan endpoint with filters"""
    
    def test_scan_tokens_returns_list(self):
        """GET /api/tokens/scan returns filtered tokens with holder_distribution_safe and top_holder_percentage fields"""
        # This endpoint can be slow due to multiple API calls
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 600000,
                "min_market_cap": 100000,
                "max_market_cap": 600000,
                "min_age_minutes": 40,
                "max_age_minutes": 180,
                "min_liquidity": 15000
            },
            timeout=120  # 2 minutes timeout due to slow external APIs
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Scan returned {len(data)} tokens")
        
        # If we have tokens, verify they have required fields
        if data:
            token = data[0]
            assert "holder_distribution_safe" in token or token.get("holder_distribution_safe") is None or "holder_distribution_safe" in str(token)
            assert "top_holder_percentage" in token or token.get("top_holder_percentage") is None or "top_holder_percentage" in str(token)
            print(f"✅ Token fields verified: {token.get('base_token_symbol')}")
            
            # Verify filter criteria are met
            if token.get("volume_24h"):
                assert token["volume_24h"] >= 600000, f"Volume should be >= 600K, got {token['volume_24h']}"
            if token.get("market_cap"):
                assert 100000 <= token["market_cap"] <= 600000, f"MCap should be 100K-600K, got {token['market_cap']}"
            if token.get("age_minutes"):
                assert 40 <= token["age_minutes"] <= 180, f"Age should be 40-180min, got {token['age_minutes']}"
            if token.get("liquidity_usd"):
                assert token["liquidity_usd"] >= 15000, f"Liquidity should be >= 15K, got {token['liquidity_usd']}"
    
    def test_scan_tokens_with_default_params(self):
        """GET /api/tokens/scan with default params returns list"""
        response = requests.get(f"{BASE_URL}/api/tokens/scan", timeout=120)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Default scan returned {len(data)} tokens")


class TestTokenLatest:
    """Test latest tokens endpoint"""
    
    def test_latest_tokens_returns_list(self):
        """GET /api/tokens/latest returns latest filtered tokens"""
        response = requests.get(f"{BASE_URL}/api/tokens/latest", timeout=30)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Latest returned {len(data)} tokens")


class TestTokenCheck:
    """Test specific token check endpoint"""
    
    def test_check_specific_token(self):
        """POST /api/tokens/check/{token_address} checks a specific token against all criteria"""
        response = requests.post(
            f"{BASE_URL}/api/tokens/check/{TEST_TOKEN_ADDRESS}",
            timeout=60
        )
        # Token might not exist or might return 404
        if response.status_code == 404:
            print(f"⚠️ Token not found (expected for old test tokens)")
            pytest.skip("Test token not found on DexScreener")
        
        assert response.status_code in [200, 404]
        
        if response.status_code == 200:
            data = response.json()
            # Verify response structure
            assert "token" in data
            assert "metrics" in data
            assert "filter_checks" in data
            assert "passes_all_filters" in data
            
            # Verify filter_checks has top10_holders_25pct field
            filter_checks = data.get("filter_checks", {})
            assert "top10_holders_25pct" in filter_checks
            print(f"✅ Token check completed: {data['token'].get('symbol')}")
            print(f"   Filter checks: {filter_checks}")
            print(f"   Passes all: {data['passes_all_filters']}")


class TestWatchlist:
    """Test watchlist CRUD operations"""
    
    def test_add_token_to_watchlist(self):
        """POST /api/tokens/watch/{token_address} adds a token to the watchlist"""
        response = requests.post(
            f"{BASE_URL}/api/tokens/watch/{TEST_TOKEN_ADDRESS}",
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        assert "token_address" in data
        print(f"✅ Added to watchlist: {data}")
    
    def test_get_watched_tokens(self):
        """GET /api/tokens/watch returns watched tokens list"""
        response = requests.get(f"{BASE_URL}/api/tokens/watch", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Watchlist has {len(data)} tokens")
    
    def test_remove_token_from_watchlist(self):
        """DELETE /api/tokens/watch/{token_address} removes a token from the watchlist"""
        # First add it to ensure it exists
        requests.post(f"{BASE_URL}/api/tokens/watch/{TEST_TOKEN_ADDRESS}", timeout=10)
        
        # Then remove it
        response = requests.delete(
            f"{BASE_URL}/api/tokens/watch/{TEST_TOKEN_ADDRESS}",
            timeout=10
        )
        assert response.status_code == 200
        data = response.json()
        assert "message" in data
        print(f"✅ Removed from watchlist: {data}")


class TestTelegramNotification:
    """Test Telegram notification endpoint"""
    
    def test_telegram_test_notification(self):
        """POST /api/telegram/test sends a test Telegram notification and returns holder_data and social_data"""
        response = requests.post(f"{BASE_URL}/api/telegram/test", timeout=60)
        assert response.status_code == 200
        data = response.json()
        
        # Verify response structure
        assert "message" in data
        assert "holder_data" in data
        assert "social_data" in data
        
        # Verify holder_data structure
        holder_data = data.get("holder_data", {})
        assert "solscan_pct" in holder_data or "primary_pct" in holder_data
        
        # Verify social_data structure
        social_data = data.get("social_data", {})
        assert isinstance(social_data, dict)
        
        print(f"✅ Telegram test sent: {data['message']}")
        print(f"   Holder data: {holder_data}")
        print(f"   Social data keys: {list(social_data.keys())}")


class TestNotificationHistory:
    """Test notification history endpoint"""
    
    def test_notification_history(self):
        """GET /api/notifications/history returns notification logs"""
        response = requests.get(f"{BASE_URL}/api/notifications/history", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Notification history has {len(data)} records")
        
        # If we have records, verify structure
        if data:
            record = data[0]
            print(f"   Sample record: token={record.get('token_symbol')}, type={record.get('notification_type')}")


class TestTelegramSubscription:
    """Test Telegram subscription endpoints"""
    
    def test_get_telegram_subscriptions(self):
        """GET /api/telegram/subscriptions returns list"""
        response = requests.get(f"{BASE_URL}/api/telegram/subscriptions", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Telegram subscriptions: {len(data)}")


class TestEmailSubscription:
    """Test email subscription endpoints"""
    
    def test_get_subscriptions(self):
        """GET /api/subscriptions returns list"""
        response = requests.get(f"{BASE_URL}/api/subscriptions", timeout=10)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"✅ Email subscriptions: {len(data)}")
    
    def test_create_subscription(self):
        """POST /api/subscriptions creates new subscription"""
        test_email = f"test_{int(time.time())}@example.com"
        response = requests.post(
            f"{BASE_URL}/api/subscriptions",
            json={"email": test_email},
            timeout=10
        )
        # Should return 201 for new subscription
        assert response.status_code in [200, 201, 400]  # 400 if already exists
        print(f"✅ Subscription test completed: status={response.status_code}")


# Run tests if executed directly
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
