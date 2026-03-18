"""
Tests for Pump.fun integration and Liq/MCap ratio filters.

Tests the following features:
- Pump.fun source integration (tokens with source="pump.fun")
- min_liq_mcap_pct and max_liq_mcap_pct filters
- Pump.fun bonding curve tokens (null liquidity) bypass liq filters
"""

import pytest
import requests
import os

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://pump-detector-7.preview.emergentagent.com').rstrip('/')


class TestPumpFunIntegration:
    """Test pump.fun integration as data source"""
    
    def test_scan_returns_pumpfun_source_field(self):
        """GET /api/tokens/scan should return tokens with source field indicating pump.fun"""
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
                "min_liquidity": 500,
                "max_liquidity": 100000,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        assert isinstance(tokens, list)
        
        # Find tokens from pump.fun
        pumpfun_tokens = [t for t in tokens if t.get("source") == "pump.fun"]
        dexscreener_tokens = [t for t in tokens if t.get("source") == "dexscreener"]
        
        print(f"✅ Total tokens: {len(tokens)}")
        print(f"   pump.fun tokens: {len(pumpfun_tokens)}")
        print(f"   dexscreener tokens: {len(dexscreener_tokens)}")
        
        # Verify source field is present
        for token in tokens[:5]:
            assert "source" in token, f"Token {token.get('base_token_symbol')} missing source field"
    
    def test_pumpfun_dexid_tokens_marked_correctly(self):
        """Tokens with dexId=pumpfun or pumpswap should have source=pump.fun"""
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        
        # Check dexId mapping to source
        for token in tokens:
            dex_id = (token.get("dex_id") or "").lower()
            source = token.get("source")
            
            if dex_id in ("pumpfun", "pumpswap"):
                assert source == "pump.fun", f"Token with dexId={dex_id} should have source=pump.fun, got {source}"
                print(f"✅ {token.get('base_token_symbol')}: dexId={dex_id} → source={source}")


class TestLiqMcapRatioFilters:
    """Test min_liq_mcap_pct and max_liq_mcap_pct filter parameters"""
    
    def test_min_liq_mcap_pct_filter(self):
        """GET /api/tokens/scan with min_liq_mcap_pct should filter tokens by minimum liq/mcap ratio"""
        # Request with min_liq_mcap_pct=5 (require at least 5% liquidity ratio)
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
                "min_liquidity": 500,
                "max_liquidity": 100000,
                "min_liq_mcap_pct": 5,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        assert isinstance(tokens, list)
        print(f"✅ With min_liq_mcap_pct=5: {len(tokens)} tokens returned")
        
        # Check that non-bonding tokens meet the criteria
        for token in tokens:
            liq = token.get("liquidity_usd", 0)
            mcap = token.get("market_cap", 0)
            dex_id = (token.get("dex_id") or "").lower()
            is_bonding = dex_id == "pumpfun" and liq == 0
            
            if not is_bonding and mcap > 0:
                ratio = (liq / mcap) * 100
                # Allow small tolerance for floating point
                assert ratio >= 4.9, f"Token {token.get('base_token_symbol')} has ratio {ratio:.1f}% < 5%"
                print(f"   ✅ {token.get('base_token_symbol')}: {ratio:.1f}% >= 5%")
    
    def test_max_liq_mcap_pct_filter(self):
        """GET /api/tokens/scan with max_liq_mcap_pct should filter tokens by maximum liq/mcap ratio"""
        # Request with max_liq_mcap_pct=30 (reject tokens with >30% liquidity ratio)
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
                "min_liquidity": 500,
                "max_liquidity": 100000,
                "max_liq_mcap_pct": 30,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        assert isinstance(tokens, list)
        print(f"✅ With max_liq_mcap_pct=30: {len(tokens)} tokens returned")
        
        # Check that non-bonding tokens meet the criteria
        for token in tokens:
            liq = token.get("liquidity_usd", 0)
            mcap = token.get("market_cap", 0)
            dex_id = (token.get("dex_id") or "").lower()
            is_bonding = dex_id == "pumpfun" and liq == 0
            
            if not is_bonding and mcap > 0:
                ratio = (liq / mcap) * 100
                assert ratio <= 30.1, f"Token {token.get('base_token_symbol')} has ratio {ratio:.1f}% > 30%"
                print(f"   ✅ {token.get('base_token_symbol')}: {ratio:.1f}% <= 30%")
    
    def test_both_liq_mcap_pct_filters_combined(self):
        """GET /api/tokens/scan with both min and max liq_mcap_pct should work together"""
        # Request with min_liq_mcap_pct=10 and max_liq_mcap_pct=50
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
                "min_liquidity": 500,
                "max_liquidity": 100000,
                "min_liq_mcap_pct": 10,
                "max_liq_mcap_pct": 50,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        assert isinstance(tokens, list)
        print(f"✅ With min=10% and max=50%: {len(tokens)} tokens returned")
        
        for token in tokens:
            liq = token.get("liquidity_usd", 0)
            mcap = token.get("market_cap", 0)
            dex_id = (token.get("dex_id") or "").lower()
            is_bonding = dex_id == "pumpfun" and liq == 0
            
            if not is_bonding and mcap > 0:
                ratio = (liq / mcap) * 100
                assert 9.9 <= ratio <= 50.1, f"Token {token.get('base_token_symbol')} has ratio {ratio:.1f}% outside 10-50%"
                print(f"   ✅ {token.get('base_token_symbol')}: {ratio:.1f}% in range")


class TestPumpFunBondingCurveHandling:
    """Test that pump.fun bonding curve tokens (null liquidity) bypass liq filters"""
    
    def test_bonding_curve_tokens_bypass_liquidity_filters(self):
        """Pump.fun bonding curve tokens with null liquidity should bypass liq/mcap filters"""
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
                "min_liq_mcap_pct": 5,  # Would filter out 0% ratio tokens
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        
        # Find bonding curve tokens (pumpfun dexId with 0 liquidity)
        bonding_tokens = [
            t for t in tokens 
            if (t.get("dex_id") or "").lower() == "pumpfun" and t.get("liquidity_usd", 0) == 0
        ]
        
        if bonding_tokens:
            print(f"✅ Found {len(bonding_tokens)} bonding curve tokens that bypassed liq filters")
            for token in bonding_tokens[:3]:
                print(f"   - {token.get('base_token_symbol')}: dexId=pumpfun, liq=0, source={token.get('source')}")
        else:
            print("⚠️ No bonding curve tokens found in current scan (may be market dependent)")
    
    def test_bonding_curve_tokens_have_correct_source(self):
        """Bonding curve tokens should still have source=pump.fun"""
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 50000,
                "min_market_cap": 5000,
                "max_market_cap": 500000,
                "max_age_minutes": 120,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        
        for token in tokens:
            dex_id = (token.get("dex_id") or "").lower()
            if dex_id == "pumpfun":
                assert token.get("source") == "pump.fun", f"Bonding token should have source=pump.fun"
                print(f"✅ Bonding token {token.get('base_token_symbol')}: source={token.get('source')}")


class TestDefaultFilterValues:
    """Test that default filter values work correctly"""
    
    def test_default_liq_mcap_filters(self):
        """Default min_liq_mcap_pct=0 and max_liq_mcap_pct=100 should not filter"""
        # Call with explicit defaults
        response = requests.get(
            f"{BASE_URL}/api/tokens/scan",
            params={
                "min_volume": 80000,
                "min_market_cap": 10000,
                "max_market_cap": 1000000,
                "max_age_minutes": 60,
                "min_liquidity": 1000,
                "max_liquidity": 100000,
                "min_liq_mcap_pct": 0,
                "max_liq_mcap_pct": 100,
            },
            timeout=120
        )
        assert response.status_code == 200
        tokens = response.json()
        print(f"✅ With default liq/mcap filters: {len(tokens)} tokens")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
