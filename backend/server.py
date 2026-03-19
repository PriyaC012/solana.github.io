from fastapi import FastAPI, APIRouter, HTTPException, BackgroundTasks
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timezone
import httpx
import asyncio
import resend
import telegram

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# In-memory cache for holder data (reduces API calls)
_holder_cache = {}  # {token_address: {"data": {...}, "ts": timestamp}}
HOLDER_CACHE_TTL = 300  # 5 minutes

# In-memory cache for screener results (30 second TTL)
_screener_cache = {"data": [], "ts": 0.0}
SCREENER_CACHE_TTL = 60  # seconds

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Resend configuration
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', 'onboarding@resend.dev')
if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY

# Telegram configuration
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')
telegram_bot = None
if TELEGRAM_BOT_TOKEN:
    telegram_bot = telegram.Bot(token=TELEGRAM_BOT_TOKEN)

# Create the main app
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# DexScreener API base URL
DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"

# Models
class TokenData(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    token_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chain_id: str = "solana"
    pair_address: str
    base_token_address: str
    base_token_name: str
    base_token_symbol: str
    price_usd: Optional[float] = 0
    price_change_24h: Optional[float] = 0
    price_change_5m: Optional[float] = 0
    price_change_1h: Optional[float] = 0
    volume_24h: Optional[float] = 0
    liquidity_usd: Optional[float] = 0
    market_cap: Optional[float] = 0
    pair_created_at: Optional[int] = None
    dex_id: Optional[str] = None
    url: Optional[str] = None
    liquidity_locked: bool = False
    top_holder_percentage: Optional[float] = None
    age_minutes: Optional[int] = None
    txns_24h: Optional[int] = 0
    buys_24h: Optional[int] = 0
    sells_24h: Optional[int] = 0
    makers_24h: Optional[int] = 0
    image_url: Optional[str] = None

class EmailSubscription(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    subscription_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    email: EmailStr
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class EmailSubscriptionCreate(BaseModel):
    email: EmailStr

class ScannerCriteria(BaseModel):
    min_volume: float = 300000
    min_market_cap: float = 10000
    max_market_cap: float = 1000000
    min_age_minutes: int = 0
    max_age_minutes: int = 1440
    min_liquidity: float = 10000
    max_liquidity: float = 100000
    min_liq_mcap_pct: float = 0
    max_liq_mcap_pct: float = 100
    min_txns_24h: int = 3000

class NotificationLog(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    log_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    token_symbol: str
    token_address: str
    email: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    notification_type: str = "email"  # "email" or "telegram"
    sent_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TelegramSubscription(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    subscription_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    chat_id: str
    phone_number: Optional[str] = None
    is_active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class TelegramSubscriptionCreate(BaseModel):
    chat_id: str
    phone_number: Optional[str] = None

class WatchedToken(BaseModel):
    model_config = ConfigDict(extra="ignore")
    
    token_address: str
    added_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    is_active: bool = True

# Helper functions
def calculate_age_minutes(pair_created_at: Optional[int]) -> Optional[int]:
    """Calculate age in minutes from pair creation timestamp"""
    if not pair_created_at:
        return None
    now = datetime.now(timezone.utc).timestamp() * 1000
    age_ms = now - pair_created_at
    return int(age_ms / 60000)

async def fetch_solana_tokens() -> List[dict]:
    """Fetch new Solana tokens from DexScreener with exponential backoff"""
    all_pairs = []
    seen_addresses = set()
    
    async def _fetch_with_backoff(client_http, url, label=""):
        """Helper with exponential backoff for rate-limited requests"""
        for attempt in range(3):
            try:
                response = await client_http.get(url)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code in (429, 403) or "1015" in response.text:
                    wait = 2 ** (attempt + 1)
                    logger.warning(f"Rate limited ({label}), waiting {wait}s...")
                    await asyncio.sleep(wait)
                else:
                    logger.warning(f"{label} returned {response.status_code}")
                    return None
            except Exception as e:
                logger.error(f"{label} error: {e}")
                return None
        return None
    
    try:
        async with httpx.AsyncClient(timeout=30.0) as client_http:
            # 1. Search queries (reduced to 3 to lower API calls)
            for query in ["solana", "pump", "meme"]:
                await asyncio.sleep(1.5)  # Increased delay between requests
                data = await _fetch_with_backoff(client_http, f"{DEXSCREENER_API}/search?q={query}", f"search-{query}")
                if data:
                    for p in data.get("pairs", []):
                        if p.get("chainId") == "solana":
                            addr = p.get("baseToken", {}).get("address", "")
                            if addr and addr not in seen_addresses:
                                seen_addresses.add(addr)
                                all_pairs.append(p)
            
            logger.info(f"Search queries: {len(all_pairs)} unique Solana pairs")
            
            # 2. Token profiles
            await asyncio.sleep(1.5)
            profiles_data = await _fetch_with_backoff(client_http, "https://api.dexscreener.com/token-profiles/latest/v1", "profiles")
            if profiles_data and isinstance(profiles_data, list):
                solana_profiles = [p for p in profiles_data if p.get("chainId") == "solana"]
                profile_addresses = [p.get("tokenAddress") for p in solana_profiles 
                                    if p.get("tokenAddress") and p.get("tokenAddress") not in seen_addresses]
                if profile_addresses:
                    await asyncio.sleep(1.5)
                    profile_pairs = await get_token_pairs_by_addresses(profile_addresses[:30])
                    for p in profile_pairs:
                        addr = p.get("baseToken", {}).get("address", "")
                        if addr and addr not in seen_addresses:
                            seen_addresses.add(addr)
                            all_pairs.append(p)
            
            # 3. Token boosts
            await asyncio.sleep(1.5)
            boosts_data = await _fetch_with_backoff(client_http, "https://api.dexscreener.com/token-boosts/latest/v1", "boosts")
            if boosts_data and isinstance(boosts_data, list):
                solana_boosts = [t for t in boosts_data if t.get("chainId") == "solana"]
                boost_addresses = [t.get("tokenAddress") for t in solana_boosts 
                                  if t.get("tokenAddress") and t.get("tokenAddress") not in seen_addresses]
                if boost_addresses:
                    await asyncio.sleep(1.5)
                    boost_pairs = await get_token_pairs_by_addresses(boost_addresses[:30])
                    for p in boost_pairs:
                        addr = p.get("baseToken", {}).get("address", "")
                        if addr and addr not in seen_addresses:
                            seen_addresses.add(addr)
                            all_pairs.append(p)
                
    except Exception as e:
        logger.error(f"Error fetching tokens: {e}")
    
    logger.info(f"Total unique Solana pairs found: {len(all_pairs)}")
    return all_pairs

async def fetch_recent_solana_profiles() -> List[dict]:
    """Fetch recent token profiles from DexScreener"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client_http:
            # Get latest boosted tokens (often new launches)
            response = await client_http.get("https://api.dexscreener.com/token-boosts/latest/v1")
            if response.status_code == 200:
                tokens = response.json()
                solana_tokens = [t for t in tokens if t.get("chainId") == "solana"]
                return solana_tokens[:50]
            return []
    except Exception as e:
        logger.error(f"Error fetching profiles: {e}")
        return []

async def get_token_pairs_by_addresses(addresses: List[str]) -> List[dict]:
    """Get detailed pair info for token addresses"""
    if not addresses:
        return []
    try:
        async with httpx.AsyncClient(timeout=30.0) as client_http:
            # DexScreener allows batch requests
            addresses_str = ",".join(addresses[:30])  # Limit to 30
            response = await client_http.get(f"{DEXSCREENER_API}/tokens/{addresses_str}")
            if response.status_code == 200:
                data = response.json()
                return data.get("pairs", [])
            return []
    except Exception as e:
        logger.error(f"Error fetching pair details: {e}")
        return []

def filter_tokens_by_criteria(pairs: List[dict], criteria: ScannerCriteria) -> List[TokenData]:
    """Filter tokens based on scanner criteria"""
    filtered = []
    
    for pair in pairs:
        try:
            # Extract data
            volume_24h = float(pair.get("volume", {}).get("h24", 0) or 0)
            market_cap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
            liquidity_usd = float(pair.get("liquidity", {}).get("usd", 0) or 0)
            pair_created_at = pair.get("pairCreatedAt")
            
            # Get transaction data
            txns = pair.get("txns", {}).get("h24", {})
            buys_24h = int(txns.get("buys", 0) or 0)
            sells_24h = int(txns.get("sells", 0) or 0)
            txns_24h = buys_24h + sells_24h
            
            # Calculate age
            age_minutes = calculate_age_minutes(pair_created_at)
            
            # Apply all filters
            # 1. Min volume
            if volume_24h < criteria.min_volume:
                continue
            # 2. Market cap range
            if market_cap < criteria.min_market_cap or market_cap > criteria.max_market_cap:
                continue
            # 3. Age range
            if age_minutes is None:
                continue
            if criteria.min_age_minutes > 0 and age_minutes < criteria.min_age_minutes:
                continue
            if age_minutes > criteria.max_age_minutes:
                continue
            # 4. Liquidity range
            if liquidity_usd < criteria.min_liquidity or liquidity_usd > criteria.max_liquidity:
                continue
            # 5. Max Liquidity/MCap ratio
            if criteria.max_liq_mcap_pct < 100 and market_cap > 0:
                if (liquidity_usd / market_cap) > (criteria.max_liq_mcap_pct / 100):
                    continue
            # 6. Min Liquidity/MCap ratio
            if criteria.min_liq_mcap_pct > 0 and market_cap > 0:
                if (liquidity_usd / market_cap) < (criteria.min_liq_mcap_pct / 100):
                    continue
            # 7. Min 24h transactions
            if criteria.min_txns_24h > 0 and txns_24h < criteria.min_txns_24h:
                continue
            
            base_token = pair.get("baseToken", {})
            info = pair.get("info", {})
            
            # Get image URL if available
            image_url = None
            if info and info.get("imageUrl"):
                image_url = info.get("imageUrl")
            
            token = TokenData(
                chain_id="solana",
                pair_address=pair.get("pairAddress", ""),
                base_token_address=base_token.get("address", ""),
                base_token_name=base_token.get("name", "Unknown"),
                base_token_symbol=base_token.get("symbol", "???"),
                price_usd=float(pair.get("priceUsd", 0) or 0),
                price_change_24h=float(pair.get("priceChange", {}).get("h24", 0) or 0),
                price_change_5m=float(pair.get("priceChange", {}).get("m5", 0) or 0),
                price_change_1h=float(pair.get("priceChange", {}).get("h1", 0) or 0),
                volume_24h=volume_24h,
                liquidity_usd=liquidity_usd,
                market_cap=market_cap,
                pair_created_at=pair_created_at,
                dex_id=pair.get("dexId", ""),
                url=pair.get("url", ""),
                age_minutes=age_minutes,
                top_holder_percentage=None,
                txns_24h=txns_24h,
                buys_24h=buys_24h,
                sells_24h=sells_24h,
                makers_24h=int(pair.get("txns", {}).get("h24", {}).get("makers", 0) or 0),
                image_url=image_url
            )
            
            filtered.append(token)
        except Exception as e:
            logger.error(f"Error processing pair: {e}")
            continue
    
    # Sort by age (newest first)
    filtered.sort(key=lambda x: x.age_minutes or 999)
    
    return filtered

async def send_email_notification(email: str, token: TokenData):
    """Send email notification for a token"""
    if not RESEND_API_KEY:
        logger.warning("No Resend API key configured")
        return False
    
    try:
        html_content = f"""
        <div style="font-family: 'JetBrains Mono', monospace; background: #0A0A0A; color: #fff; padding: 20px;">
            <h2 style="color: #14F195; margin-bottom: 20px;">🚀 New Token Alert!</h2>
            <div style="background: #121212; border: 1px solid rgba(20, 241, 149, 0.3); padding: 20px;">
                <h3 style="color: #fff; margin: 0 0 15px 0;">{token.base_token_symbol} ({token.base_token_name})</h3>
                <table style="width: 100%; border-collapse: collapse;">
                    <tr>
                        <td style="padding: 8px 0; color: #888;">Price:</td>
                        <td style="padding: 8px 0; color: #14F195; text-align: right;">${token.price_usd:.8f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #888;">Volume (24h):</td>
                        <td style="padding: 8px 0; color: #00C2FF; text-align: right;">${token.volume_24h:,.0f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #888;">Market Cap:</td>
                        <td style="padding: 8px 0; color: #9945FF; text-align: right;">${token.market_cap:,.0f}</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #888;">Age:</td>
                        <td style="padding: 8px 0; color: #FFB800; text-align: right;">{token.age_minutes} minutes</td>
                    </tr>
                    <tr>
                        <td style="padding: 8px 0; color: #888;">Contract:</td>
                        <td style="padding: 8px 0; color: #fff; text-align: right; font-size: 12px;">{token.base_token_address[:20]}...</td>
                    </tr>
                </table>
                <div style="margin-top: 20px;">
                    <a href="https://dexscreener.com/solana/{token.pair_address}" 
                       style="display: inline-block; background: #14F195; color: #000; padding: 10px 20px; text-decoration: none; font-weight: bold;">
                        View on DexScreener
                    </a>
                    <a href="https://solscan.io/token/{token.base_token_address}" 
                       style="display: inline-block; background: #9945FF; color: #fff; padding: 10px 20px; text-decoration: none; font-weight: bold; margin-left: 10px;">
                        View on Solscan
                    </a>
                </div>
            </div>
            <p style="color: #666; font-size: 12px; margin-top: 20px;">
                This alert was triggered because the token meets your criteria: 50K+ volume, 50K+ market cap, launched within 15 minutes.
            </p>
        </div>
        """
        
        params = {
            "from": SENDER_EMAIL,
            "to": [email],
            "subject": f"🚀 New Solana Token: {token.base_token_symbol} - ${token.market_cap:,.0f} MC",
            "html": html_content
        }
        
        email_result = await asyncio.to_thread(resend.Emails.send, params)
        
        # Log notification
        log = NotificationLog(
            token_symbol=token.base_token_symbol,
            token_address=token.base_token_address,
            email=email,
            notification_type="email"
        )
        log_doc = log.model_dump()
        log_doc['sent_at'] = log_doc['sent_at'].isoformat()
        await db.notification_logs.insert_one(log_doc)
        
        logger.info(f"Email sent to {email} for token {token.base_token_symbol}")
        return True
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False

async def get_solscan_holders(token_address: str) -> dict:
    """Get top holders using Solana RPC getTokenLargestAccounts (free, no API key needed)"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client_http:
            rpc_url = "https://api.mainnet-beta.solana.com"
            
            # 1. Get total supply (with retry)
            total_supply = 0
            for attempt in range(3):
                supply_resp = await client_http.post(
                    rpc_url,
                    json={"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [token_address]}
                )
                if supply_resp.status_code == 200:
                    supply_data = supply_resp.json()
                    if "error" not in supply_data:
                        total_supply = float(supply_data.get("result", {}).get("value", {}).get("amount", 0) or 0)
                        break
                elif supply_resp.status_code == 429:
                    await asyncio.sleep(2 * (attempt + 1))
                else:
                    break
            
            await asyncio.sleep(0.5)  # Small delay between RPC calls to avoid rate limiting
            
            # 2. Get largest accounts (with retry)
            for attempt in range(3):
                holders_resp = await client_http.post(
                    rpc_url,
                    json={"jsonrpc": "2.0", "id": 2, "method": "getTokenLargestAccounts", "params": [token_address]}
                )
                if holders_resp.status_code == 200:
                    holders_data = holders_resp.json()
                    if "error" not in holders_data:
                        accounts = holders_data.get("result", {}).get("value", [])
                        
                        total_percentage = 0.0
                        holder_details = []
                        
                        for i, account in enumerate(accounts[:10]):
                            amount = float(account.get("amount", 0) or 0)
                            pct = (amount / total_supply * 100) if total_supply > 0 else 0
                            if pct > 0:
                                total_percentage += pct
                                holder_details.append({
                                    "rank": i + 1,
                                    "address": (account.get("address", "") or "")[:8] + "...",
                                    "percentage": round(pct, 2)
                                })
                        
                        logger.info(f"Solana RPC holders for {token_address[:12]}...: top10={round(total_percentage, 2)}%")
                        return {
                            "top10_percentage": round(total_percentage, 2),
                            "holder_count": len(accounts),
                            "holders": holder_details,
                            "source": "solana_rpc"
                        }
                    break
                elif holders_resp.status_code == 429:
                    await asyncio.sleep(1 * (attempt + 1))
                else:
                    break
            
            return {"top10_percentage": 0, "holder_count": 0, "holders": [], "source": "solana_rpc"}
    except Exception as e:
        logger.error(f"Solana RPC holder check error: {e}")
        return {"top10_percentage": 0, "holder_count": 0, "holders": [], "source": "solana_rpc"}

async def check_rugcheck(token_address: str) -> dict:
    """Check token risk and top holders using rugcheck.xyz API"""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client_http:
            url = f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report"
            response = await client_http.get(url)
            
            if response.status_code == 200:
                data = response.json()
                
                # Get risk info
                score = data.get("score", 0)
                risks = data.get("risks", [])
                
                # Get top holders info
                top_holders = data.get("topHolders", [])
                total_top10_percentage = 0.0
                
                # Calculate top 10 holders percentage
                for i, holder in enumerate(top_holders[:10]):
                    pct = holder.get("pct", 0) or holder.get("percentage", 0) or 0
                    total_top10_percentage += float(pct)
                
                # Determine risk status based on score
                # Rugcheck scores: higher = safer (Good: >1000, Danger: <500)
                risk_status = "UNKNOWN"
                
                if score > 0:
                    if score >= 700:
                        risk_status = "GOOD ✅"
                    elif score >= 400:
                        risk_status = "WARNING ⚡"
                    else:
                        risk_status = "DANGER ⚠️"
                
                # Check for critical risks that override score
                critical_keywords = ["rug", "scam", "honeypot", "mint authority", "freeze authority", "high ownership"]
                for risk in risks:
                    risk_name = (risk.get("name", "") or risk.get("description", "")).lower()
                    risk_level = (risk.get("level", "") or "").lower()
                    if risk_level in ["critical", "danger", "high", "error"]:
                        risk_status = "DANGER ⚠️"
                        break
                    for keyword in critical_keywords:
                        if keyword in risk_name:
                            risk_status = "DANGER ⚠️"
                            break
                
                # If top 10 holders > 30%, mark as danger
                if total_top10_percentage > 30:
                    risk_status = "DANGER ⚠️"
                
                return {
                    "status": risk_status,
                    "score": score,
                    "top10_percentage": round(total_top10_percentage, 2),
                    "risks_count": len(risks),
                    "holder_count": len(top_holders),
                    "passes_holder_check": total_top10_percentage <= 30,
                    "source": "rugcheck"
                }
            else:
                logger.warning(f"Rugcheck API returned {response.status_code} for {token_address}")
                return {
                    "status": "UNKNOWN",
                    "score": 0,
                    "top10_percentage": 0,
                    "risks_count": 0,
                    "holder_count": 0,
                    "passes_holder_check": False,
                    "source": "rugcheck"
                }
    except Exception as e:
        logger.error(f"Rugcheck API error for {token_address}: {e}")
        return {
            "status": "UNKNOWN",
            "score": 0,
            "top10_percentage": 0,
            "risks_count": 0,
            "holder_count": 0,
            "passes_holder_check": False,
            "source": "rugcheck"
        }

async def check_lp_locked(token_address: str) -> dict:
    """Check if LP tokens are burned/locked. Uses Rugcheck markets to get LP mint, then Solana RPC to check supply."""
    now = datetime.now(timezone.utc).timestamp()
    cache_key = f"lp_{token_address}"
    
    cached = _holder_cache.get(cache_key)
    if cached and (now - cached["ts"]) < HOLDER_CACHE_TTL:
        return cached["data"]
    
    result = {"locked": False, "lp_mint": None, "lp_supply": None, "source": "unknown"}
    
    try:
        # Get LP mint from Rugcheck markets
        async with httpx.AsyncClient(timeout=15.0) as client_http:
            rugcheck_resp = await client_http.get(f"https://api.rugcheck.xyz/v1/tokens/{token_address}/report")
            if rugcheck_resp.status_code == 200:
                data = rugcheck_resp.json()
                markets = data.get("markets", [])
                
                for market in markets:
                    lp_mint = market.get("mintLP")
                    if not lp_mint:
                        continue
                    
                    # Check LP token supply via Solana RPC
                    for attempt in range(2):
                        rpc_resp = await client_http.post(
                            "https://api.mainnet-beta.solana.com",
                            json={"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply", "params": [lp_mint]}
                        )
                        if rpc_resp.status_code == 200:
                            rpc_data = rpc_resp.json()
                            if "error" not in rpc_data:
                                supply = float(rpc_data.get("result", {}).get("value", {}).get("amount", "1") or "1")
                                result["lp_mint"] = lp_mint
                                result["lp_supply"] = supply
                                result["locked"] = supply == 0
                                result["source"] = "solana_rpc"
                                logger.info(f"LP check {token_address[:12]}...: mint={lp_mint[:12]}... supply={supply} locked={supply == 0}")
                                break
                        elif rpc_resp.status_code == 429:
                            await asyncio.sleep(1.5)
                        else:
                            break
                    
                    if result["lp_mint"]:
                        break
                
                # Also check totalLPProviders from rugcheck
                if not result["locked"] and data.get("totalLPProviders", -1) == 0:
                    result["locked"] = True
                    result["source"] = "rugcheck_lp_providers"
                    logger.info(f"LP locked via rugcheck totalLPProviders=0 for {token_address[:12]}...")
    except Exception as e:
        logger.error(f"LP lock check error: {e}")
    
    _holder_cache[cache_key] = {"data": result, "ts": now}
    return result


async def check_holder_distribution(token_address: str) -> dict:
    """Check holder distribution: Solana RPC and Rugcheck in parallel — use whichever responds first with data."""
    now = datetime.now(timezone.utc).timestamp()
    
    # Check cache
    cached = _holder_cache.get(token_address)
    if cached and (now - cached["ts"]) < HOLDER_CACHE_TTL:
        return cached["data"]
    
    # Call both in parallel — use whichever has data
    solscan_task = get_solscan_holders(token_address)
    rugcheck_task = check_rugcheck(token_address)
    solscan, rugcheck = await asyncio.gather(solscan_task, rugcheck_task, return_exceptions=True)
    
    if isinstance(solscan, Exception):
        solscan = {"top10_percentage": 0}
    if isinstance(rugcheck, Exception):
        rugcheck = {"top10_percentage": 0}
    
    solscan_pct = solscan.get("top10_percentage", 0)
    rugcheck_pct = rugcheck.get("top10_percentage", 0)
    
    # Use whichever has data (non-zero first)
    if solscan_pct > 0:
        primary_pct = solscan_pct
        primary_source = "solana_rpc"
    elif rugcheck_pct > 0:
        primary_pct = rugcheck_pct
        primary_source = "rugcheck"
    else:
        primary_pct = 0
        primary_source = "unknown"
    
    # Pass/fail/unknown — threshold is 30%
    if primary_pct > 0:
        passes = primary_pct <= 30
    else:
        passes = True  # Can't verify — let it through
    
    result = {
        "solscan_pct": solscan_pct,
        "rugcheck_pct": rugcheck_pct,
        "primary_pct": primary_pct,
        "primary_source": primary_source,
        "passes": passes,
        "rugcheck_status": rugcheck.get("status", "UNKNOWN"),
        "rugcheck_score": rugcheck.get("score", 0),
    }
    
    _holder_cache[token_address] = {"data": result, "ts": now}
    return result


async def send_telegram_notification(chat_id: str, token: TokenData, holder_data: dict = None):
    """Send Telegram notification for a token. Never blocks — token already passed all checks."""
    if not telegram_bot:
        logger.warning("No Telegram bot configured")
        return False
    
    try:
        # Format message
        price_str = f"${token.price_usd:.8f}" if token.price_usd < 0.01 else f"${token.price_usd:.4f}"
        change_emoji = "+" if (token.price_change_24h or 0) >= 0 else ""
        liq_mcap_ratio = (token.liquidity_usd / token.market_cap * 100) if token.market_cap > 0 else 0
        
        addr = token.base_token_address
        pair = token.pair_address
        
        message = (
            f"<b>NEW TOKEN ALERT</b>\n\n"
            f"<b>{token.base_token_symbol}</b> ({token.base_token_name})\n\n"
            f"Price: <code>{price_str}</code>\n"
            f"24h: <code>{change_emoji}{token.price_change_24h or 0:.2f}%</code>\n"
            f"Volume: <code>${token.volume_24h:,.0f}</code>\n"
            f"MCap: <code>${token.market_cap:,.0f}</code>\n"
            f"Liquidity: <code>${token.liquidity_usd:,.0f}</code> ({liq_mcap_ratio:.1f}% of MCap)\n"
            f"Age: <code>{token.age_minutes} min</code>\n"
            f"TXNs: <code>{token.txns_24h or 0}</code>\n\n"
            f"Contract:\n<code>{addr}</code>\n\n"
            f"<a href=\"https://dexscreener.com/solana/{pair}\">DexScreener</a>"
            f" | <a href=\"https://solscan.io/token/{addr}\">Solscan</a>"
            f" | <a href=\"https://rugcheck.xyz/tokens/{addr}\">Rugcheck</a>"
            f" | <a href=\"https://pump.fun/{addr}\">Pump.fun</a>"
        )
        
        await telegram_bot.send_message(
            chat_id=chat_id,
            text=message,
            parse_mode=telegram.constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
        
        # Log notification
        log = NotificationLog(
            token_symbol=token.base_token_symbol,
            token_address=token.base_token_address,
            telegram_chat_id=chat_id,
            notification_type="telegram"
        )
        log_doc = log.model_dump()
        log_doc['sent_at'] = log_doc['sent_at'].isoformat()
        await db.notification_logs.insert_one(log_doc)
        
        logger.info(f"Telegram SENT to {chat_id} for {token.base_token_symbol}")
        return True
    except Exception as e:
        logger.error(f"Error sending Telegram to {chat_id}: {e}")
        return False

async def notify_subscribers(tokens: List[TokenData]):
    """Notify all subscribers about new tokens. Tokens already passed all filters."""
    logger.info(f"notify_subscribers called with {len(tokens)} tokens")
    
    # Get Telegram subscribers
    telegram_subscribers = await db.telegram_subscriptions.find(
        {"is_active": True},
        {"_id": 0}
    ).to_list(1000)
    
    # Get email subscribers
    email_subscribers = await db.email_subscriptions.find(
        {"is_active": True}, 
        {"_id": 0}
    ).to_list(1000)
    
    logger.info(f"Subscribers: {len(email_subscribers)} email, {len(telegram_subscribers)} Telegram, default_chat={TELEGRAM_CHAT_ID}")
    
    for token in tokens:
        logger.info(f"Processing notification for: {token.base_token_symbol} ({token.base_token_address[:16]}...)")
        
        # Check if we already notified about this token
        existing = await db.notification_logs.find_one({
            "token_address": token.base_token_address
        }, {"_id": 0})
        
        if existing:
            logger.info(f"Already notified about {token.base_token_symbol}, skipping")
            continue
        
        # Get cached LP data for notification
        lp_cache_key = f"lp_{token.base_token_address}"
        
        # Send to all Telegram subscribers
        for sub in telegram_subscribers:
            logger.info(f"Sending Telegram to subscriber: {sub['chat_id']}")
            await send_telegram_notification(sub["chat_id"], token)
        
        # Send to default Telegram chat if configured and not already a subscriber
        if TELEGRAM_CHAT_ID and not any(s.get("chat_id") == TELEGRAM_CHAT_ID for s in telegram_subscribers):
            logger.info(f"Sending Telegram to default chat: {TELEGRAM_CHAT_ID}")
            await send_telegram_notification(TELEGRAM_CHAT_ID, token)
        
        # Send email notifications
        for sub in email_subscribers:
            await send_email_notification(sub["email"], token)

# API Routes
@api_router.get("/")
async def root():
    return {"message": "Solana Token Scanner API"}

@api_router.get("/tokens/scan")
async def scan_tokens(
    background_tasks: BackgroundTasks,
    min_volume: float = 80000,
    min_market_cap: float = 10000,
    max_market_cap: float = 1000000,
    min_age_minutes: int = 0,
    max_age_minutes: int = 60,
    min_liquidity: float = 1000,
    max_liquidity: float = 100000,
    min_liq_mcap_pct: float = 0,
    max_liq_mcap_pct: float = 100
):
    """Scan for new Solana tokens matching criteria"""
    criteria = ScannerCriteria(
        min_volume=min_volume,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_age_minutes=min_age_minutes,
        max_age_minutes=max_age_minutes,
        min_liquidity=min_liquidity,
        max_liquidity=max_liquidity,
        min_liq_mcap_pct=min_liq_mcap_pct,
        max_liq_mcap_pct=max_liq_mcap_pct
    )
    
    # Fetch from multiple sources
    search_pairs = await fetch_solana_tokens()
    
    # Also get recent profiles
    recent_profiles = await fetch_recent_solana_profiles()
    profile_addresses = [p.get("tokenAddress") for p in recent_profiles if p.get("tokenAddress")]
    profile_pairs = await get_token_pairs_by_addresses(profile_addresses)
    
    # Get watched tokens
    watched_tokens = await db.watched_tokens.find(
        {"is_active": True},
        {"_id": 0}
    ).to_list(100)
    watched_addresses = [t.get("token_address") for t in watched_tokens if t.get("token_address")]
    watched_pairs = []
    if watched_addresses:
        watched_pairs = await get_token_pairs_by_addresses(watched_addresses)
        logger.info(f"Watched tokens: {len(watched_pairs)} pairs")
    
    # Combine and deduplicate
    all_pairs = search_pairs + profile_pairs + watched_pairs
    seen = set()
    unique_pairs = []
    for pair in all_pairs:
        addr = pair.get("baseToken", {}).get("address", "")
        if addr and addr not in seen:
            seen.add(addr)
            unique_pairs.append(pair)
    
    logger.info(f"Total unique pairs to filter: {len(unique_pairs)}")
    
    # Apply filters (volume, mcap, age, liquidity, liq/mcap ratio)
    candidates = filter_tokens_by_criteria(unique_pairs, criteria)
    logger.info(f"Tokens matching all criteria: {len(candidates)}")
    
    # Send notifications in background
    if candidates:
        background_tasks.add_task(notify_subscribers, candidates)
    
    return candidates

@api_router.get("/tokens/latest")
async def get_latest_solana_tokens():
    """Get latest Solana token launches with filters applied"""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client_http:
            # Get latest boosted tokens
            response = await client_http.get("https://api.dexscreener.com/token-boosts/latest/v1")
            if response.status_code == 200:
                tokens = response.json()
                solana_tokens = [t for t in tokens if t.get("chainId") == "solana"]
                
                # Get pair details
                addresses = [t.get("tokenAddress") for t in solana_tokens[:30] if t.get("tokenAddress")]
                if addresses:
                    pairs = await get_token_pairs_by_addresses(addresses)
                    
                    result = []
                    for pair in pairs:
                        if pair.get("chainId") != "solana":
                            continue
                        
                        base_token = pair.get("baseToken", {})
                        age = calculate_age_minutes(pair.get("pairCreatedAt"))
                        volume = float(pair.get("volume", {}).get("h24", 0) or 0)
                        market_cap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
                        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
                        
                        # Get transaction data
                        txns = pair.get("txns", {}).get("h24", {})
                        buys = int(txns.get("buys", 0) or 0)
                        sells = int(txns.get("sells", 0) or 0)
                        total_txns = buys + sells
                        
                        # Apply filters:
                        # Age <= 60 mins
                        if age is None or age > 60:
                            continue
                        # Min volume 80K
                        if volume < 80000:
                            continue
                        # Market cap between 10K and 1M
                        if market_cap < 10000 or market_cap > 1000000:
                            continue
                        # Liquidity between 1K and 100K
                        if liquidity < 1000 or liquidity > 100000:
                            continue
                        
                        # Get image
                        info = pair.get("info", {})
                        image_url = info.get("imageUrl") if info else None
                        
                        result.append({
                            "symbol": base_token.get("symbol", "???"),
                            "name": base_token.get("name", "Unknown"),
                            "address": base_token.get("address", ""),
                            "pair_address": pair.get("pairAddress", ""),
                            "price_usd": float(pair.get("priceUsd", 0) or 0),
                            "price_change_24h": float(pair.get("priceChange", {}).get("h24", 0) or 0),
                            "price_change_5m": float(pair.get("priceChange", {}).get("m5", 0) or 0),
                            "price_change_1h": float(pair.get("priceChange", {}).get("h1", 0) or 0),
                            "volume_24h": volume,
                            "market_cap": market_cap,
                            "liquidity_usd": liquidity,
                            "age_minutes": age,
                            "dex_id": pair.get("dexId", ""),
                            "url": pair.get("url", ""),
                            "txns_24h": total_txns,
                            "buys_24h": buys,
                            "sells_24h": sells,
                            "makers_24h": int(txns.get("makers", 0) or 0),
                            "image_url": image_url
                        })
                    
                    # Sort by age (newest first)
                    result.sort(key=lambda x: x.get("age_minutes") or 9999)
                    return result
            return []
    except Exception as e:
        logger.error(f"Error: {e}")
        return []

@api_router.post("/subscriptions", response_model=EmailSubscription, status_code=201)
async def create_subscription(input: EmailSubscriptionCreate):
    """Subscribe email for token alerts"""
    # Check if already subscribed
    existing = await db.email_subscriptions.find_one(
        {"email": input.email},
        {"_id": 0}
    )
    
    if existing:
        if existing.get("is_active"):
            raise HTTPException(status_code=400, detail="Email already subscribed")
        else:
            # Reactivate subscription
            await db.email_subscriptions.update_one(
                {"email": input.email},
                {"$set": {"is_active": True}}
            )
            existing["is_active"] = True
            return EmailSubscription(**existing)
    
    subscription = EmailSubscription(email=input.email)
    doc = subscription.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.email_subscriptions.insert_one(doc)
    return subscription

@api_router.delete("/subscriptions/{email}")
async def unsubscribe(email: str):
    """Unsubscribe email from alerts"""
    result = await db.email_subscriptions.update_one(
        {"email": email},
        {"$set": {"is_active": False}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    return {"message": "Unsubscribed successfully"}

@api_router.get("/subscriptions", response_model=List[EmailSubscription])
async def get_subscriptions():
    """Get all active subscriptions"""
    subs = await db.email_subscriptions.find(
        {"is_active": True},
        {"_id": 0}
    ).to_list(1000)
    
    for sub in subs:
        if isinstance(sub.get('created_at'), str):
            sub['created_at'] = datetime.fromisoformat(sub['created_at'])
    
    return subs

@api_router.get("/notifications/history")
async def get_notification_history(limit: int = 50):
    """Get notification history"""
    logs = await db.notification_logs.find(
        {},
        {"_id": 0}
    ).sort("sent_at", -1).to_list(limit)
    
    return logs

# Telegram subscription endpoints
@api_router.post("/telegram/subscribe", response_model=TelegramSubscription, status_code=201)
async def create_telegram_subscription(input: TelegramSubscriptionCreate):
    """Subscribe Telegram chat for token alerts"""
    # Check if already subscribed
    existing = await db.telegram_subscriptions.find_one(
        {"chat_id": input.chat_id},
        {"_id": 0}
    )
    
    if existing:
        if existing.get("is_active"):
            raise HTTPException(status_code=400, detail="Chat already subscribed")
        else:
            # Reactivate subscription
            await db.telegram_subscriptions.update_one(
                {"chat_id": input.chat_id},
                {"$set": {"is_active": True}}
            )
            existing["is_active"] = True
            if isinstance(existing.get('created_at'), str):
                existing['created_at'] = datetime.fromisoformat(existing['created_at'])
            return TelegramSubscription(**existing)
    
    subscription = TelegramSubscription(chat_id=input.chat_id, phone_number=input.phone_number)
    doc = subscription.model_dump()
    doc['created_at'] = doc['created_at'].isoformat()
    
    await db.telegram_subscriptions.insert_one(doc)
    
    # Send welcome message
    if telegram_bot:
        try:
            await telegram_bot.send_message(
                chat_id=input.chat_id,
                text="Subscribed to Solana Token Scanner!\n\nYou'll receive alerts when new tokens match your configured filters.\n\nDefault: Vol >= $80K, MCap $10K-$1M, Liq $1K-$100K, Age &lt;= 1h"
            )
        except Exception as e:
            logger.error(f"Failed to send welcome message: {e}")
    
    return subscription

@api_router.delete("/telegram/unsubscribe/{chat_id}")
async def telegram_unsubscribe(chat_id: str):
    """Unsubscribe Telegram chat from alerts"""
    result = await db.telegram_subscriptions.update_one(
        {"chat_id": chat_id},
        {"$set": {"is_active": False}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Subscription not found")
    
    return {"message": "Unsubscribed successfully"}

@api_router.get("/telegram/subscriptions", response_model=List[TelegramSubscription])
async def get_telegram_subscriptions():
    """Get all active Telegram subscriptions"""
    subs = await db.telegram_subscriptions.find(
        {"is_active": True},
        {"_id": 0}
    ).to_list(1000)
    
    for sub in subs:
        if isinstance(sub.get('created_at'), str):
            sub['created_at'] = datetime.fromisoformat(sub['created_at'])
    
    return subs

@api_router.post("/telegram/test")
async def test_telegram_notification():
    """Send a test notification to configured Telegram chat"""
    if not telegram_bot or not TELEGRAM_CHAT_ID:
        raise HTTPException(status_code=400, detail="Telegram not configured")
    
    try:
        test_address = "Aj21QKXezLit9kdJzPXfRrozhuKSgoLaDJBY6zbspump"
        holder = await check_holder_distribution(test_address)
        
        message = (
            "<b>Test Notification - Scanner Active</b>\n\n"
            "<b>Holder Data (Solana RPC first):</b>\n"
            f"  Solana RPC: {holder.get('solscan_pct', 0)}%\n"
            f"  Rugcheck: {holder.get('rugcheck_pct', 0)}%\n"
            f"  Primary ({holder.get('primary_source', 'unknown')}): {holder.get('primary_pct', 0)}%\n"
            f"  Rugcheck Status: {holder.get('rugcheck_status', 'UNKNOWN')}\n\n"
            "<b>Active filters:</b>\n"
            "  Vol >= $80K\n"
            "  MCap $10K-$1M\n"
            "  Liq $1K-$100K\n"
            "  Age &lt;= 1h\n\n"
            "Refresh: Every 30 seconds"
        )
        
        await telegram_bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text=message,
            parse_mode=telegram.constants.ParseMode.HTML,
            disable_web_page_preview=True
        )
        return {"message": "Test notification sent", "holder_data": holder}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed: {str(e)}")

@api_router.post("/tokens/check/{token_address}")
async def check_specific_token(token_address: str, background_tasks: BackgroundTasks):
    """Check a specific token address and send alert if it matches criteria"""
    try:
        pairs = await get_token_pairs_by_addresses([token_address])
        
        if not pairs:
            raise HTTPException(status_code=404, detail="Token not found on DexScreener")
        
        pair = pairs[0]
        if pair.get("chainId") != "solana":
            raise HTTPException(status_code=400, detail="Not a Solana token")
        
        base_token = pair.get("baseToken", {})
        volume = float(pair.get("volume", {}).get("h24", 0) or 0)
        market_cap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        age = calculate_age_minutes(pair.get("pairCreatedAt"))
        liq_ratio = (liquidity / market_cap * 100) if market_cap > 0 else 0
        
        checks = {
            "volume_80k": volume >= 80000,
            "mcap_10k_1m": 10000 <= market_cap <= 1000000,
            "liquidity_1k_100k": 1000 <= liquidity <= 100000,
            "age_under_60_mins": age is not None and age <= 60,
        }
        
        all_pass = all(checks.values())
        
        result = {
            "token": {
                "symbol": base_token.get("symbol"),
                "name": base_token.get("name"),
                "address": token_address,
                "pair_address": pair.get("pairAddress")
            },
            "metrics": {
                "volume_24h": volume,
                "market_cap": market_cap,
                "liquidity": liquidity,
                "liq_mcap_ratio": round(liq_ratio, 2),
                "age_minutes": age,
            },
            "filter_checks": checks,
            "passes_all_filters": all_pass
        }
        
        # If passes all filters, trigger notification
        if all_pass:
            txns = pair.get("txns", {}).get("h24", {})
            token_data = TokenData(
                chain_id="solana",
                pair_address=pair.get("pairAddress", ""),
                base_token_address=token_address,
                base_token_name=base_token.get("name", "Unknown"),
                base_token_symbol=base_token.get("symbol", "???"),
                price_usd=float(pair.get("priceUsd", 0) or 0),
                price_change_24h=float(pair.get("priceChange", {}).get("h24", 0) or 0),
                price_change_5m=float(pair.get("priceChange", {}).get("m5", 0) or 0),
                price_change_1h=float(pair.get("priceChange", {}).get("h1", 0) or 0),
                volume_24h=volume,
                liquidity_usd=liquidity,
                market_cap=market_cap,
                pair_created_at=pair.get("pairCreatedAt"),
                dex_id=pair.get("dexId", ""),
                url=pair.get("url", ""),
                age_minutes=age,
                txns_24h=int(txns.get("buys", 0) or 0) + int(txns.get("sells", 0) or 0),
                buys_24h=int(txns.get("buys", 0) or 0),
                sells_24h=int(txns.get("sells", 0) or 0)
            )
            
            background_tasks.add_task(notify_subscribers, [token_data])
            result["notification_triggered"] = True
        else:
            result["notification_triggered"] = False
        
        return result
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking token: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/tokens/lookup/{token_address}")
async def lookup_token(token_address: str):
    """Look up a specific token without sending alerts"""
    try:
        pairs = await get_token_pairs_by_addresses([token_address])
        
        if not pairs:
            raise HTTPException(status_code=404, detail="Token not found")
        
        pair = pairs[0]
        base_token = pair.get("baseToken", {})
        volume = float(pair.get("volume", {}).get("h24", 0) or 0)
        market_cap = float(pair.get("marketCap", 0) or pair.get("fdv", 0) or 0)
        liquidity = float(pair.get("liquidity", {}).get("usd", 0) or 0)
        age = calculate_age_minutes(pair.get("pairCreatedAt"))
        liq_ratio = (liquidity / market_cap * 100) if market_cap > 0 else 0
        
        # Get rugcheck data
        rugcheck = await check_rugcheck(token_address)
        
        return {
            "symbol": base_token.get("symbol"),
            "name": base_token.get("name"),
            "address": token_address,
            "price_usd": float(pair.get("priceUsd", 0) or 0),
            "volume_24h": volume,
            "market_cap": market_cap,
            "liquidity": liquidity,
            "liq_mcap_ratio": round(liq_ratio, 2),
            "age_minutes": age,
            "dex_id": pair.get("dexId"),
            "pair_address": pair.get("pairAddress"),
            "url": pair.get("url"),
            "rugcheck": rugcheck,
            "filter_check": {
                "volume_600k": volume >= 600000,
                "mcap_100k_600k": 100000 <= market_cap <= 600000,
                "liquidity_15k": liquidity >= 15000,
                "liq_under_50pct": liq_ratio < 50,
                "age_40_180": age is not None and 40 <= age <= 180,
                "top10_holders_25pct": rugcheck.get("passes_holder_check", False)
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/tokens/watch/{token_address}")
async def add_watched_token(token_address: str):
    """Add a token to the watch list for automatic scanning"""
    # Check if already watched
    existing = await db.watched_tokens.find_one(
        {"token_address": token_address},
        {"_id": 0}
    )
    
    if existing:
        if existing.get("is_active"):
            return {"message": "Token already being watched", "token_address": token_address}
        else:
            # Reactivate
            await db.watched_tokens.update_one(
                {"token_address": token_address},
                {"$set": {"is_active": True}}
            )
            return {"message": "Token watch reactivated", "token_address": token_address}
    
    # Add new watched token
    watched = WatchedToken(token_address=token_address)
    doc = watched.model_dump()
    doc['added_at'] = doc['added_at'].isoformat()
    await db.watched_tokens.insert_one(doc)
    
    return {"message": "Token added to watch list", "token_address": token_address}

@api_router.delete("/tokens/watch/{token_address}")
async def remove_watched_token(token_address: str):
    """Remove a token from the watch list"""
    result = await db.watched_tokens.update_one(
        {"token_address": token_address},
        {"$set": {"is_active": False}}
    )
    
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Token not in watch list")
    
    return {"message": "Token removed from watch list", "token_address": token_address}

@api_router.get("/tokens/watch")
async def get_watched_tokens():
    """Get all watched tokens"""
    watched = await db.watched_tokens.find(
        {"is_active": True},
        {"_id": 0}
    ).to_list(100)

    return watched

async def fetch_dexscreener_new_pairs(
    min_liquidity: float = 10000,
    max_liquidity: float = 100000,
    min_market_cap: float = 10000,
    max_market_cap: float = 1000000,
    max_age_days: int = 1,
    min_txns_24h: int = 3000,
    min_volume: float = 300000,
) -> List[dict]:
    """
    Fetch the newest Solana pairs from DexScreener using only 2 lightweight API calls:
      1. GET /token-profiles/latest/v1  → newest token addresses (rarely rate-limited)
      2. GET /latest/dex/tokens/{addrs} → batch pair data for those addresses

    Mirrors the DexScreener new-pairs page:
    https://dexscreener.com/new-pairs/solana?rankBy=trendingScoreH6&order=desc
    &minLiq=10000&maxLiq=100000&minMarketCap=10000&maxMarketCap=1000000
    &maxAge=1&min24HTxns=3000&min24HVol=300000&profile=0

    Results are cached for 60 seconds to stay well within rate limits.
    """
    import time as _time

    global _screener_cache
    if _time.time() - _screener_cache["ts"] < SCREENER_CACHE_TTL and _screener_cache["data"]:
        logger.info(f"Returning {len(_screener_cache['data'])} cached screener pairs")
        return _screener_cache["data"]

    all_pairs: List[dict] = []

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:

            # ── Step 1: get newest Solana token addresses from profiles endpoint ──
            resp = await client.get("https://api.dexscreener.com/token-profiles/latest/v1")
            if resp.status_code != 200:
                logger.warning(f"token-profiles returned {resp.status_code}")
                resp = None
            else:
                profiles = resp.json() if resp else []
                solana_addrs = [
                    p["tokenAddress"]
                    for p in (profiles if isinstance(profiles, list) else [])
                    if p.get("chainId") == "solana" and p.get("tokenAddress")
                ]
                logger.info(f"Got {len(solana_addrs)} solana addresses from profiles")

                # ── Step 2: batch pair lookup (max 30 per call, do up to 2 batches) ──
                seen_addrs: set = set()
                for batch_start in range(0, min(len(solana_addrs), 60), 30):
                    batch = solana_addrs[batch_start:batch_start + 30]
                    await asyncio.sleep(1)  # small pause between batches
                    try:
                        r2 = await client.get(
                            f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
                        )
                        if r2.status_code == 200:
                            data = r2.json()
                            for p in data.get("pairs", []):
                                if (
                                    isinstance(p, dict)
                                    and p.get("chainId") == "solana"
                                    and p.get("pairAddress") not in seen_addrs
                                ):
                                    seen_addrs.add(p["pairAddress"])
                                    all_pairs.append(p)
                        else:
                            logger.warning(f"tokens batch returned {r2.status_code}")
                    except Exception as e:
                        logger.warning(f"Batch lookup error: {e}")

    except Exception as e:
        logger.error(f"fetch_dexscreener_new_pairs error: {e}")

    logger.info(f"Fetched {len(all_pairs)} unique new pairs for screener")
    _screener_cache["data"] = all_pairs
    _screener_cache["ts"] = _time.time()
    return all_pairs


@api_router.get("/tokens/screener")
async def get_screener_tokens(
    min_volume: float = 300000,
    min_market_cap: float = 10000,
    max_market_cap: float = 1000000,
    min_age_minutes: int = 0,
    max_age_minutes: int = 1440,
    min_liquidity: float = 10000,
    max_liquidity: float = 100000,
    min_txns_24h: int = 3000,
    min_liq_mcap_pct: float = 0,
    max_liq_mcap_pct: float = 100,
):
    """
    Screenscrape DexScreener new-pairs/solana page and return filtered tokens.
    Source URL: https://dexscreener.com/new-pairs/solana?rankBy=trendingScoreH6&...
    """
    # Convert max_age_minutes to days for the URL (round up to nearest day, min 1)
    max_age_days = max(1, (max_age_minutes + 1439) // 1440)

    pairs = await fetch_dexscreener_new_pairs(
        min_liquidity=min_liquidity,
        max_liquidity=max_liquidity,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        max_age_days=max_age_days,
        min_txns_24h=min_txns_24h,
        min_volume=min_volume,
    )

    criteria = ScannerCriteria(
        min_volume=min_volume,
        min_market_cap=min_market_cap,
        max_market_cap=max_market_cap,
        min_age_minutes=min_age_minutes,
        max_age_minutes=max_age_minutes,
        min_liquidity=min_liquidity,
        max_liquidity=max_liquidity,
        min_liq_mcap_pct=min_liq_mcap_pct,
        max_liq_mcap_pct=max_liq_mcap_pct,
        min_txns_24h=min_txns_24h,
    )

    filtered = filter_tokens_by_criteria(pairs, criteria)
    return [t.model_dump() for t in filtered]

# Include the router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
