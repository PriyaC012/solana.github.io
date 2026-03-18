import requests
import sys
import json
from datetime import datetime

class SolanaTokenScannerAPITester:
    def __init__(self, base_url="https://pump-detector-7.preview.emergentagent.com/api"):
        self.base_url = base_url
        self.tests_run = 0
        self.tests_passed = 0
        self.test_results = []

    def log_test(self, name, success, response_data=None, error_msg=None, status_code=None):
        """Log test results"""
        self.tests_run += 1
        if success:
            self.tests_passed += 1
        
        result = {
            "test_name": name,
            "success": success,
            "status_code": status_code,
            "error": error_msg,
            "timestamp": datetime.now().isoformat()
        }
        if response_data:
            result["response_sample"] = str(response_data)[:200] if len(str(response_data)) > 200 else response_data
        
        self.test_results.append(result)
        
        status = "✅ PASS" if success else "❌ FAIL"
        print(f"\n{status} - {name}")
        if status_code:
            print(f"   Status Code: {status_code}")
        if error_msg:
            print(f"   Error: {error_msg}")

    def run_test(self, name, method, endpoint, expected_status=200, data=None, params=None):
        """Run a single API test"""
        url = f"{self.base_url}/{endpoint}"
        headers = {'Content-Type': 'application/json'}
        
        try:
            if method == 'GET':
                response = requests.get(url, headers=headers, params=params, timeout=30)
            elif method == 'POST':
                response = requests.post(url, json=data, headers=headers, timeout=30)
            elif method == 'DELETE':
                response = requests.delete(url, headers=headers, timeout=30)

            success = response.status_code == expected_status
            
            try:
                response_data = response.json()
            except:
                response_data = response.text

            self.log_test(name, success, response_data, 
                         None if success else f"Expected {expected_status}, got {response.status_code}", 
                         response.status_code)

            return success, response_data

        except requests.exceptions.RequestException as e:
            self.log_test(name, False, None, f"Request failed: {str(e)}", None)
            return False, {}

    def test_root_endpoint(self):
        """Test the root API endpoint"""
        return self.run_test("Root API endpoint", "GET", "")

    def test_tokens_scan_endpoint(self):
        """Test the tokens scan endpoint with default parameters"""
        success, data = self.run_test("Tokens scan endpoint", "GET", "tokens/scan")
        
        if success and isinstance(data, list):
            print(f"   Found {len(data)} tokens matching criteria")
            if len(data) > 0:
                # Verify token structure
                sample_token = data[0]
                required_fields = ['base_token_symbol', 'base_token_address', 'price_usd', 'volume_24h', 'market_cap']
                missing_fields = [field for field in required_fields if field not in sample_token]
                if missing_fields:
                    print(f"   ⚠️  Missing fields in token data: {missing_fields}")
                else:
                    print(f"   Sample token: {sample_token.get('base_token_symbol', 'N/A')} - ${sample_token.get('price_usd', 0)}")
        
        return success

    def test_tokens_scan_with_params(self):
        """Test tokens scan with custom parameters"""
        params = {
            'min_volume': 50000,
            'min_market_cap': 50000,
            'max_age_minutes': 15
        }
        return self.run_test("Tokens scan with parameters", "GET", "tokens/scan", params=params)

    def test_tokens_latest_endpoint(self):
        """Test the latest tokens endpoint"""
        success, data = self.run_test("Latest tokens endpoint", "GET", "tokens/latest")
        
        if success and isinstance(data, list):
            print(f"   Found {len(data)} latest tokens")
            if len(data) > 0:
                sample_token = data[0]
                print(f"   Sample token: {sample_token.get('symbol', 'N/A')} - ${sample_token.get('price_usd', 0)}")
        
        return success

    def test_subscription_create(self):
        """Test creating email subscription"""
        test_email = f"test_{datetime.now().strftime('%H%M%S')}@example.com"
        data = {"email": test_email}
        
        success, response = self.run_test("Create email subscription", "POST", "subscriptions", 201, data)
        
        if success:
            print(f"   Subscription created for: {test_email}")
            subscription_id = response.get('subscription_id')
            print(f"   Subscription ID: {subscription_id}")
            return test_email  # Return email for cleanup
        
        return None

    def test_subscription_duplicate(self):
        """Test creating duplicate subscription"""
        test_email = f"duplicate_{datetime.now().strftime('%H%M%S')}@example.com"
        data = {"email": test_email}
        
        # Create first subscription
        success1, _ = self.run_test("Create first subscription", "POST", "subscriptions", 201, data)
        
        if success1:
            # Try to create duplicate - should fail with 400
            success2, response = self.run_test("Create duplicate subscription", "POST", "subscriptions", 400, data)
            return success2
        
        return False

    def test_get_subscriptions(self):
        """Test getting all subscriptions"""
        success, data = self.run_test("Get all subscriptions", "GET", "subscriptions")
        
        if success and isinstance(data, list):
            print(f"   Found {len(data)} active subscriptions")
        
        return success

    def test_unsubscribe(self, email):
        """Test unsubscribing an email"""
        if not email:
            return False
        
        return self.run_test("Unsubscribe email", "DELETE", f"subscriptions/{email}")

    def test_notification_history(self):
        """Test getting notification history"""
        success, data = self.run_test("Notification history", "GET", "notifications/history")
        
        if success and isinstance(data, list):
            print(f"   Found {len(data)} notification logs")
        
        return success

    def test_invalid_endpoint(self):
        """Test invalid endpoint returns 404"""
        return self.run_test("Invalid endpoint", "GET", "invalid/endpoint", 404)

    def run_all_tests(self):
        """Run comprehensive API tests"""
        print("🚀 Starting Solana Token Scanner API Tests")
        print(f"📍 Testing endpoint: {self.base_url}")
        print("=" * 60)
        
        # Basic connectivity
        self.test_root_endpoint()
        
        # Token endpoints
        self.test_tokens_scan_endpoint()
        self.test_tokens_scan_with_params()
        self.test_tokens_latest_endpoint()
        
        # Subscription management
        test_email = self.test_subscription_create()
        self.test_subscription_duplicate()
        self.test_get_subscriptions()
        
        # Cleanup: unsubscribe test email
        if test_email:
            self.test_unsubscribe(test_email)
        
        # Additional endpoints
        self.test_notification_history()
        self.test_invalid_endpoint()
        
        # Print summary
        print("\n" + "=" * 60)
        print("📊 TEST SUMMARY")
        print("=" * 60)
        print(f"✅ Tests Passed: {self.tests_passed}")
        print(f"❌ Tests Failed: {self.tests_run - self.tests_passed}")
        print(f"📈 Success Rate: {(self.tests_passed/self.tests_run)*100:.1f}%")
        
        if self.tests_passed < self.tests_run:
            print("\n🔍 Failed Tests:")
            for result in self.test_results:
                if not result['success']:
                    print(f"   • {result['test_name']}: {result.get('error', 'Unknown error')}")
        
        return self.tests_passed, self.tests_run

def main():
    tester = SolanaTokenScannerAPITester()
    passed, total = tester.run_all_tests()
    
    # Write detailed results to file
    with open('/app/backend_test_results.json', 'w') as f:
        json.dump({
            'summary': {
                'tests_passed': passed,
                'tests_total': total,
                'success_rate': (passed/total)*100 if total > 0 else 0,
                'timestamp': datetime.now().isoformat()
            },
            'detailed_results': tester.test_results
        }, f, indent=2)
    
    return 0 if passed == total else 1

if __name__ == "__main__":
    sys.exit(main())