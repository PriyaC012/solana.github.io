import { useState, useEffect, useCallback } from "react";
import "@/App.css";
import axios from "axios";
import { motion, AnimatePresence } from "framer-motion";
import { 
  RefreshCw, Mail, ExternalLink, Copy, Check, Clock, AlertCircle,
  Loader2, Zap, Search, ChevronUp, ChevronDown, ArrowUpDown, Settings, Save
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { 
  Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle, DialogTrigger,
} from "@/components/ui/dialog";
import { Toaster, toast } from "sonner";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL || "http://localhost:8001";

// Default filters matching the DexScreener new-pairs source URL:
// https://dexscreener.com/new-pairs/solana?rankBy=trendingScoreH6&order=desc
// &minLiq=10000&maxLiq=100000&minMarketCap=10000&maxMarketCap=1000000
// &maxAge=1&min24HTxns=3000&min24HVol=300000&profile=0
const DEFAULT_FILTERS = {
  min_volume: 300000,
  min_market_cap: 10000,
  max_market_cap: 1000000,
  min_age_minutes: 0,
  max_age_minutes: 1440,
  min_liquidity: 10000,
  max_liquidity: 100000,
  min_txns_24h: 3000,
  min_liq_mcap_pct: 0,
  max_liq_mcap_pct: 100,
  liq_mcap_enabled: false,
};

const loadFilters = () => {
  try {
    const saved = localStorage.getItem("scanner_filters");
    if (saved) return { ...DEFAULT_FILTERS, ...JSON.parse(saved) };
  } catch {}
  return DEFAULT_FILTERS;
};

const formatNumber = (num) => {
  if (!num) return "$0";
  if (num >= 1000000) return `$${(num / 1000000).toFixed(2)}M`;
  if (num >= 1000) return `$${(num / 1000).toFixed(1)}K`;
  return `$${num.toFixed(2)}`;
};

const formatPrice = (price) => {
  if (!price) return "$0";
  if (price < 0.00000001) return `$${price.toExponential(2)}`;
  if (price < 0.0001) return `$${price.toFixed(8)}`;
  if (price < 0.01) return `$${price.toFixed(6)}`;
  return `$${price.toFixed(4)}`;
};

const formatAge = (minutes) => {
  if (!minutes) return "?";
  if (minutes < 60) return `${minutes}m`;
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
};

const formatFilterVal = (val) => {
  if (val >= 1000000) return `$${(val / 1000000).toFixed(val % 1000000 === 0 ? 0 : 1)}M`;
  if (val >= 1000) return `$${(val / 1000).toFixed(val % 1000 === 0 ? 0 : 0)}K`;
  return `$${val}`;
};

const formatAgeFilter = (min, max) => {
  const fmtMin = min >= 60 ? `${min / 60}h` : `${min}m`;
  const fmtMax = max >= 60 ? `${max / 60}h` : `${max}m`;
  if (min === 0) return `≤${fmtMax}`;
  return `${fmtMin}-${fmtMax}`;
};

const PriceChange = ({ value }) => {
  const isPositive = value >= 0;
  return (
    <span className={`text-xs font-mono ${isPositive ? 'text-[#00fc6c]' : 'text-[#ff4757]'}`}>
      {isPositive ? '+' : ''}{(value || 0).toFixed(2)}%
    </span>
  );
};

const TokenRow = ({ token, index }) => {
  const [copied, setCopied] = useState(false);
  const copyAddress = async (e) => {
    e.stopPropagation();
    await navigator.clipboard.writeText(token.base_token_address || token.address);
    setCopied(true);
    toast.success("Address copied!");
    setTimeout(() => setCopied(false), 2000);
  };
  const openDex = () => window.open(token.url || `https://dexscreener.com/solana/${token.pair_address}`, '_blank');

  return (
    <motion.tr
      initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
      transition={{ delay: index * 0.03 }}
      className="border-b border-[#1e2130] hover:bg-[#1a1d2e] cursor-pointer transition-colors"
      onClick={openDex}
      data-testid={`token-row-${token.base_token_symbol || token.symbol}`}
    >
      <td className="py-3 px-4">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-full bg-[#2a2d3e] flex items-center justify-center">
            <span className="text-xs font-bold text-gray-400">
              {(token.base_token_symbol || token.symbol || "?").charAt(0)}
            </span>
          </div>
          <div>
            <div className="flex items-center gap-2">
              <span className="font-semibold text-white text-sm">{token.base_token_symbol || token.symbol}</span>
              <span className="text-[10px] px-1.5 py-0.5 bg-[#2a2d3e] text-gray-400 rounded">{token.dex_id || 'DEX'}</span>
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              <span className="text-xs text-gray-500 truncate max-w-[100px]">{token.base_token_name || token.name}</span>
              <button onClick={copyAddress} className="p-0.5 hover:bg-[#2a2d3e] rounded" data-testid={`copy-${token.base_token_symbol}`}>
                {copied ? <Check className="w-3 h-3 text-[#00fc6c]" /> : <Copy className="w-3 h-3 text-gray-500 hover:text-gray-300" />}
              </button>
            </div>
          </div>
        </div>
      </td>
      <td className="py-3 px-4"><span className="font-mono text-sm text-white">{formatPrice(token.price_usd)}</span></td>
      <td className="py-3 px-4">
        <div className="flex items-center gap-1">
          <Clock className="w-3 h-3 text-gray-500" />
          <span className="font-mono text-sm text-yellow-400">{formatAge(token.age_minutes)}</span>
        </div>
      </td>
      <td className="py-3 px-4"><PriceChange value={token.price_change_5m} /></td>
      <td className="py-3 px-4"><PriceChange value={token.price_change_1h} /></td>
      <td className="py-3 px-4"><PriceChange value={token.price_change_24h} /></td>
      <td className="py-3 px-4"><span className="font-mono text-sm text-cyan-400">{formatNumber(token.liquidity_usd)}</span></td>
      <td className="py-3 px-4"><span className="font-mono text-sm text-purple-400">{formatNumber(token.market_cap)}</span></td>
      <td className="py-3 px-4"><span className="font-mono text-sm text-blue-400">{formatNumber(token.volume_24h)}</span></td>
      <td className="py-3 px-4">
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-white">{(token.txns_24h || 0).toLocaleString()}</span>
          <div className="flex items-center gap-1 text-[10px]">
            <span className="text-[#00fc6c]">{token.buys_24h || 0}</span>
            <span className="text-gray-600">/</span>
            <span className="text-[#ff4757]">{token.sells_24h || 0}</span>
          </div>
        </div>
      </td>
      <td className="py-3 px-4">
        <div className="flex items-center gap-2">
          <a href={token.url || `https://dexscreener.com/solana/${token.pair_address}`} target="_blank" rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()} className="p-1.5 bg-[#00fc6c]/10 hover:bg-[#00fc6c]/20 rounded" data-testid={`dex-link-${token.base_token_symbol}`}>
            <ExternalLink className="w-3 h-3 text-[#00fc6c]" />
          </a>
          <a href={`https://solscan.io/token/${token.base_token_address || token.address}`} target="_blank" rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()} className="p-1.5 bg-purple-500/10 hover:bg-purple-500/20 rounded">
            <Search className="w-3 h-3 text-purple-400" />
          </a>
        </div>
      </td>
    </motion.tr>
  );
};

// Configurable Filter Bar
const FilterBar = ({ filters, onOpenSettings }) => {
  const items = [
    { label: "Vol", value: `≥${formatFilterVal(filters.min_volume)}`, color: "text-blue-400" },
    { label: "MCap", value: `${formatFilterVal(filters.min_market_cap)}-${formatFilterVal(filters.max_market_cap)}`, color: "text-purple-400" },
    { label: "Liq", value: `${formatFilterVal(filters.min_liquidity)}-${formatFilterVal(filters.max_liquidity)}`, color: "text-cyan-400" },
    { label: "Age", value: formatAgeFilter(filters.min_age_minutes, filters.max_age_minutes), color: "text-yellow-400" },
    { label: "TXNs", value: `≥${(filters.min_txns_24h || 0).toLocaleString()}`, color: "text-orange-400" },
  ];
  if (filters.liq_mcap_enabled && (filters.min_liq_mcap_pct > 0 || filters.max_liq_mcap_pct < 100)) {
    const parts = [];
    if (filters.min_liq_mcap_pct > 0) parts.push(`${filters.min_liq_mcap_pct}%`);
    if (filters.max_liq_mcap_pct < 100) parts.push(`${filters.max_liq_mcap_pct}%`);
    const label = filters.min_liq_mcap_pct > 0 && filters.max_liq_mcap_pct < 100
      ? `Liq ${filters.min_liq_mcap_pct}-${filters.max_liq_mcap_pct}%MCap`
      : filters.min_liq_mcap_pct > 0 ? `Liq>=${filters.min_liq_mcap_pct}%MCap` : `Liq<=${filters.max_liq_mcap_pct}%MCap`;
    items.push({ label, value: "\u2713", color: "text-[#00fc6c]" });
  }

  return (
    <div className="flex flex-wrap items-center gap-3 mb-4 text-xs">
      {items.map((item, i) => (
        <div key={i} className="flex items-center gap-1.5 px-2 py-1 bg-[#1a1d2e] border border-[#2a2d3e] rounded"
          data-testid={`filter-${item.label.toLowerCase()}`}>
          <span className="text-gray-500">{item.label}:</span>
          <span className={`font-mono ${item.color}`}>{item.value}</span>
        </div>
      ))}
      <button onClick={onOpenSettings}
        className="flex items-center gap-1.5 px-2.5 py-1 bg-[#1a1d2e] border border-[#2a2d3e] rounded hover:border-[#00fc6c] transition-colors"
        data-testid="open-settings">
        <Settings className="w-3 h-3 text-gray-400" />
        <span className="text-gray-400">Edit</span>
      </button>
    </div>
  );
};

// Filter input row
const FilterInput = ({ label, children }) => (
  <div className="space-y-1.5">
    <Label className="text-xs text-gray-400">{label}</Label>
    {children}
  </div>
);

// Settings Dialog
const SettingsDialog = ({ open, onOpenChange, filters, onSave }) => {
  const [draft, setDraft] = useState(filters);

  useEffect(() => { setDraft(filters); }, [filters, open]);

  const update = (key, val) => setDraft(prev => ({ ...prev, [key]: val }));
  const numChange = (key) => (e) => {
    const v = e.target.value;
    update(key, v === "" ? "" : Number(v));
  };

  const handleSave = () => {
    const cleaned = { ...draft };
    Object.keys(cleaned).forEach(k => {
      if (cleaned[k] === "") cleaned[k] = DEFAULT_FILTERS[k];
    });
    onSave(cleaned);
    onOpenChange(false);
    toast.success("Filters updated!");
  };

  const handleReset = () => {
    setDraft(DEFAULT_FILTERS);
  };

  const inputCls = "h-8 text-xs bg-[#1a1d2e] border-[#2a2d3e] focus:border-[#00fc6c] text-white font-mono";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="bg-[#12141f] border border-[#2a2d3e] text-white sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-base flex items-center gap-2">
            <Settings className="w-4 h-4 text-[#00fc6c]" /> Scanner Filters
          </DialogTitle>
          <DialogDescription className="text-gray-500 text-xs">
            Configure scan criteria. Changes apply immediately and persist across sessions.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-2 gap-4 mt-3" data-testid="settings-panel">
          <FilterInput label="Min Volume ($)">
            <Input type="number" value={draft.min_volume} onChange={numChange("min_volume")} className={inputCls} data-testid="input-min-volume" />
          </FilterInput>

          <FilterInput label="Min TXNs (24h)">
            <Input type="number" value={draft.min_txns_24h} onChange={numChange("min_txns_24h")} className={inputCls} data-testid="input-min-txns" />
          </FilterInput>

          <FilterInput label="Min Market Cap ($)">
            <Input type="number" value={draft.min_market_cap} onChange={numChange("min_market_cap")} className={inputCls} data-testid="input-min-mcap" />
          </FilterInput>
          <FilterInput label="Max Market Cap ($)">
            <Input type="number" value={draft.max_market_cap} onChange={numChange("max_market_cap")} className={inputCls} data-testid="input-max-mcap" />
          </FilterInput>

          <FilterInput label="Min Liquidity ($)">
            <Input type="number" value={draft.min_liquidity} onChange={numChange("min_liquidity")} className={inputCls} data-testid="input-min-liq" />
          </FilterInput>
          <FilterInput label="Max Liquidity ($)">
            <Input type="number" value={draft.max_liquidity} onChange={numChange("max_liquidity")} className={inputCls} data-testid="input-max-liq" />
          </FilterInput>

          <FilterInput label="Min Age (minutes)">
            <Input type="number" value={draft.min_age_minutes} onChange={numChange("min_age_minutes")} className={inputCls} data-testid="input-min-age" />
          </FilterInput>
          <FilterInput label="Max Age (minutes)">
            <Input type="number" value={draft.max_age_minutes} onChange={numChange("max_age_minutes")} className={inputCls} data-testid="input-max-age" />
          </FilterInput>

          <div className="col-span-2 border-t border-[#2a2d3e] pt-3">
            <div className="flex items-center justify-between mb-2">
              <Label className="text-xs text-gray-400">Liq/MCap Ratio Filter</Label>
              <Switch checked={draft.liq_mcap_enabled} onCheckedChange={(v) => update("liq_mcap_enabled", v)} data-testid="toggle-liq-mcap" />
            </div>
            {draft.liq_mcap_enabled && (
              <div className="grid grid-cols-2 gap-4">
                <FilterInput label="Min Liq as % of MCap">
                  <Input type="number" value={draft.min_liq_mcap_pct} onChange={numChange("min_liq_mcap_pct")}
                    className={inputCls} min={0} max={100} data-testid="input-min-liq-mcap-pct" />
                </FilterInput>
                <FilterInput label="Max Liq as % of MCap">
                  <Input type="number" value={draft.max_liq_mcap_pct} onChange={numChange("max_liq_mcap_pct")}
                    className={inputCls} min={1} max={100} data-testid="input-max-liq-mcap-pct" />
                </FilterInput>
              </div>
            )}
          </div>
        </div>

        <div className="flex items-center justify-between mt-4 pt-3 border-t border-[#2a2d3e]">
          <Button variant="ghost" size="sm" onClick={handleReset}
            className="text-xs text-gray-400 hover:text-white" data-testid="reset-filters">
            Reset to defaults
          </Button>
          <Button onClick={handleSave} size="sm"
            className="bg-[#00fc6c] text-black hover:bg-[#00fc6c]/90 font-semibold text-xs" data-testid="save-filters">
            <Save className="w-3 h-3 mr-1.5" /> Apply Filters
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
};

const EmailSubscriptionDialog = ({ onSubscribe }) => {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState(false);
  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!email) return;
    setLoading(true);
    try { await onSubscribe(email); setEmail(""); setOpen(false); }
    catch {} finally { setLoading(false); }
  };
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="bg-purple-500/20 border border-purple-500/30 text-purple-400 hover:bg-purple-500/30 text-xs h-8" data-testid="subscribe-button">
          <Mail className="w-3 h-3 mr-1.5" /> Alerts
        </Button>
      </DialogTrigger>
      <DialogContent className="bg-[#12141f] border border-[#2a2d3e] text-white sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="text-lg">Email Alerts</DialogTitle>
          <DialogDescription className="text-gray-400 text-sm">Get notified when new tokens match criteria</DialogDescription>
        </DialogHeader>
        <form onSubmit={handleSubmit} className="space-y-4 mt-4">
          <Input type="email" placeholder="your@email.com" value={email} onChange={(e) => setEmail(e.target.value)}
            className="bg-[#1a1d2e] border-[#2a2d3e] focus:border-[#00fc6c] text-white font-mono" data-testid="email-input" />
          <Button type="submit" disabled={loading || !email}
            className="w-full bg-[#00fc6c] text-black hover:bg-[#00fc6c]/90 font-semibold" data-testid="submit-subscription">
            {loading ? <Loader2 className="w-4 h-4 animate-spin mr-2" /> : <Zap className="w-4 h-4 mr-2" />} Subscribe
          </Button>
        </form>
      </DialogContent>
    </Dialog>
  );
};

const SortHeader = ({ label, sortKey, currentSort, onSort }) => {
  const isActive = currentSort.key === sortKey;
  return (
    <th className="py-3 px-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider cursor-pointer hover:text-white transition-colors"
      onClick={() => onSort(sortKey)}>
      <div className="flex items-center gap-1">
        {label}
        {isActive ? (currentSort.dir === 'asc' ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />) : <ArrowUpDown className="w-3 h-3 opacity-30" />}
      </div>
    </th>
  );
};

function App() {
  const [tokens, setTokens] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [countdown, setCountdown] = useState(30);
  const [isPaused, setIsPaused] = useState(false);
  const [lastUpdate, setLastUpdate] = useState(null);
  const [sort, setSort] = useState({ key: 'age_minutes', dir: 'asc' });
  const [searchTerm, setSearchTerm] = useState("");
  const [filters, setFilters] = useState(loadFilters);
  const [settingsOpen, setSettingsOpen] = useState(false);

  const saveFilters = (newFilters) => {
    setFilters(newFilters);
    localStorage.setItem("scanner_filters", JSON.stringify(newFilters));
    // Trigger immediate refresh with new filters
    fetchTokensWithFilters(newFilters);
    setCountdown(30);
  };

  const fetchTokensWithFilters = useCallback(async (f) => {
    try {
      setLoading(true);
      setError(null);

      // Call backend screener endpoint which screenscrapes:
      // https://dexscreener.com/new-pairs/solana?rankBy=trendingScoreH6&order=desc&...
      const params = {
        min_volume: f.min_volume,
        min_market_cap: f.min_market_cap,
        max_market_cap: f.max_market_cap,
        min_age_minutes: f.min_age_minutes,
        max_age_minutes: f.max_age_minutes,
        min_liquidity: f.min_liquidity,
        max_liquidity: f.max_liquidity,
        min_txns_24h: f.min_txns_24h || 0,
        min_liq_mcap_pct: f.liq_mcap_enabled ? (f.min_liq_mcap_pct || 0) : 0,
        max_liq_mcap_pct: f.liq_mcap_enabled ? (f.max_liq_mcap_pct || 100) : 100,
      };

      const response = await axios.get(`${BACKEND_URL}/api/tokens/screener`, { params, timeout: 60000 });
      const tokens = Array.isArray(response.data) ? response.data : [];

      setTokens(tokens);
      setLastUpdate(new Date());
    } catch (err) {
      console.error("Error fetching tokens:", err);
      setError("Failed to fetch tokens from screener");
    } finally {
      setLoading(false);
    }
  }, []);

  const fetchTokens = useCallback(() => {
    fetchTokensWithFilters(filters);
  }, [filters, fetchTokensWithFilters]);

  const handleSubscribe = async (_email) => {
    toast.info("Email alerts require a backend server. This feature is not available on the static deployment.");
    throw new Error("No backend");
  };

  const handleSort = (key) => {
    setSort(prev => ({ key, dir: prev.key === key && prev.dir === 'asc' ? 'desc' : 'asc' }));
  };

  const filteredTokens = tokens
    .filter(t => {
      if (!searchTerm) return true;
      const s = (t.base_token_symbol || t.symbol || '').toLowerCase();
      const n = (t.base_token_name || t.name || '').toLowerCase();
      return s.includes(searchTerm.toLowerCase()) || n.includes(searchTerm.toLowerCase());
    })
    .sort((a, b) => {
      const aVal = a[sort.key] || 0;
      const bVal = b[sort.key] || 0;
      return sort.dir === 'asc' ? aVal - bVal : bVal - aVal;
    });

  useEffect(() => { fetchTokens(); }, [fetchTokens]);

  useEffect(() => {
    if (isPaused) return;
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) { fetchTokens(); return 30; }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [isPaused, fetchTokens]);

  return (
    <div className="min-h-screen bg-[#0d0e14] text-white">
      <header className="h-14 border-b border-[#1e2130] flex items-center justify-between px-4 bg-[#12141f] sticky top-0 z-40">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 bg-gradient-to-br from-[#00fc6c] to-[#00c853] rounded-lg flex items-center justify-center">
            <Zap className="w-4 h-4 text-black" />
          </div>
          <div>
            <h1 className="font-bold text-sm text-white tracking-tight">Solana Token Scanner</h1>
            <p className="text-[10px] text-gray-500">New Launches • Real-time</p>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <div className="relative hidden md:block">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-500" />
            <Input type="text" placeholder="Search tokens..." value={searchTerm} onChange={(e) => setSearchTerm(e.target.value)}
              className="w-48 h-8 pl-8 text-xs bg-[#1a1d2e] border-[#2a2d3e] focus:border-[#00fc6c]" data-testid="search-input" />
          </div>
          <div className="flex items-center gap-1.5 px-2.5 py-1 bg-[#00fc6c]/10 border border-[#00fc6c]/20 rounded">
            <div className="w-1.5 h-1.5 bg-[#00fc6c] rounded-full animate-pulse" />
            <span className="text-[10px] font-medium text-[#00fc6c]">LIVE</span>
          </div>
          <button className="flex items-center gap-1.5 px-2.5 py-1 bg-[#1a1d2e] border border-[#2a2d3e] rounded hover:border-[#3a3d4e]"
            onClick={() => setIsPaused(!isPaused)} data-testid="refresh-timer">
            <RefreshCw className={`w-3 h-3 text-cyan-400 ${!isPaused && 'animate-spin'}`} style={{ animationDuration: '3s' }} />
            <span className="font-mono text-xs text-white">{countdown}s</span>
          </button>
          <Button onClick={() => { fetchTokens(); setCountdown(30); }} variant="outline" size="sm"
            className="h-8 bg-transparent border-[#2a2d3e] hover:border-[#00fc6c] hover:bg-[#00fc6c]/10 text-white" data-testid="manual-refresh">
            <RefreshCw className="w-3.5 h-3.5" />
          </Button>
          <EmailSubscriptionDialog onSubscribe={handleSubscribe} />
        </div>
      </header>

      <main className="p-4 max-w-[1800px] mx-auto">
        <FilterBar filters={filters} onOpenSettings={() => setSettingsOpen(true)} />
        <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} filters={filters} onSave={saveFilters} />

        <div className="flex items-center justify-between mb-4 text-xs">
          <div className="flex items-center gap-4">
            <span className="text-gray-400"><span className="text-[#00fc6c] font-mono">{filteredTokens.length}</span> tokens</span>
            {lastUpdate && <span className="text-gray-600">Updated: {lastUpdate.toLocaleTimeString()}</span>}
          </div>
          {error && <div className="flex items-center gap-1.5 text-[#ff4757]"><AlertCircle className="w-3.5 h-3.5" />{error}</div>}
        </div>

        <div className="bg-[#12141f] border border-[#1e2130] rounded-lg overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full" data-testid="token-table">
              <thead className="bg-[#0d0e14] border-b border-[#1e2130]">
                <tr>
                  <th className="py-3 px-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Token</th>
                  <SortHeader label="Price" sortKey="price_usd" currentSort={sort} onSort={handleSort} />
                  <SortHeader label="Age" sortKey="age_minutes" currentSort={sort} onSort={handleSort} />
                  <th className="py-3 px-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">5m</th>
                  <th className="py-3 px-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">1h</th>
                  <th className="py-3 px-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">24h</th>
                  <SortHeader label="Liquidity" sortKey="liquidity_usd" currentSort={sort} onSort={handleSort} />
                  <SortHeader label="MCap" sortKey="market_cap" currentSort={sort} onSort={handleSort} />
                  <SortHeader label="Volume" sortKey="volume_24h" currentSort={sort} onSort={handleSort} />
                  <SortHeader label="TXNs" sortKey="txns_24h" currentSort={sort} onSort={handleSort} />
                  <th className="py-3 px-4 text-left text-xs font-medium text-gray-400 uppercase tracking-wider">Links</th>
                </tr>
              </thead>
              <tbody>
                {loading && tokens.length === 0 ? (
                  [...Array(5)].map((_, i) => (
                    <tr key={i} className="border-b border-[#1e2130]">
                      <td colSpan={11} className="py-4 px-4"><div className="h-8 bg-[#1a1d2e] rounded animate-pulse" /></td>
                    </tr>
                  ))
                ) : filteredTokens.length === 0 ? (
                  <tr>
                    <td colSpan={11} className="py-16 text-center">
                      <div className="flex flex-col items-center gap-3">
                        <div className="w-12 h-12 bg-[#1a1d2e] rounded-full flex items-center justify-center">
                          <AlertCircle className="w-6 h-6 text-gray-500" />
                        </div>
                        <div>
                          <p className="font-medium text-white">No tokens match criteria</p>
                          <p className="text-sm text-gray-500 mt-1">Waiting for new launches that meet all filters...</p>
                        </div>
                      </div>
                    </td>
                  </tr>
                ) : (
                  <AnimatePresence>
                    {filteredTokens.map((token, index) => (
                      <TokenRow key={token.token_id || token.pair_address || index} token={token} index={index} />
                    ))}
                  </AnimatePresence>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="mt-6 flex items-center justify-between text-xs text-gray-600">
          <p>Data scraped from DexScreener New Pairs • Not financial advice</p>
          <div className="flex items-center gap-4">
            <a href="https://dexscreener.com/solana" target="_blank" rel="noopener noreferrer" className="hover:text-[#00fc6c] transition-colors">DexScreener</a>
            <a href="https://solscan.io" target="_blank" rel="noopener noreferrer" className="hover:text-purple-400 transition-colors">Solscan</a>
          </div>
        </div>
      </main>

      <Toaster position="bottom-right" toastOptions={{ style: { background: '#12141f', color: '#fff', border: '1px solid #2a2d3e' } }} />
    </div>
  );
}

export default App;
