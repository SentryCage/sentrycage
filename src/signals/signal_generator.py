"""
Signal generator for creating trading signals from analyzed transactions
"""
from enum import Enum
from typing import Dict, List, Any, Optional
import html
import logging
from datetime import datetime
from copy import deepcopy

from src.analysis.transaction_analyzer import TransactionDirection
from src.config.known_addresses import CEX_ADDRESS_LIST, get_exchange_name

# Configure logger
logger = logging.getLogger(__name__)

# Block explorer base URLs per chain (used to build clickable links in alerts)
EXPLORER_URLS = {
    "ethereum": "https://etherscan.io",
    "arbitrum": "https://arbiscan.io",
}


def explorer_tx_url(chain: str, tx_hash: str) -> Optional[str]:
    """Return the explorer URL for a transaction, or None if chain is unknown."""
    base = EXPLORER_URLS.get((chain or "").lower())
    return f"{base}/tx/{tx_hash}" if base else None


def explorer_address_url(chain: str, address: str) -> Optional[str]:
    """Return the explorer URL for an address, or None if chain is unknown."""
    base = EXPLORER_URLS.get((chain or "").lower())
    return f"{base}/address/{address}" if base else None

class SignalType(Enum):
    """Types of trading signals"""
    ACCUMULATION = "accumulation"  # Whale accumulating a position
    DISTRIBUTION = "distribution"  # Whale distributing a position
    TRANSFER = "transfer"  # Large transfer between wallets
    EXCHANGE_DEPOSIT = "exchange_deposit"  # Deposit to exchange (potential sell)
    EXCHANGE_WITHDRAWAL = "exchange_withdrawal"  # Withdrawal from exchange (potential buy)
    UNUSUAL_ACTIVITY = "unusual_activity"  # Unusual pattern detected

class SignalStrength(Enum):
    """Signal strength indicators"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    VERY_HIGH = "very_high"

class Signal:
    """Trading signal generated from transaction analysis"""
    
    def __init__(
        self,
        signal_type: SignalType,
        strength: SignalStrength,
        transaction_hash: str,
        wallet_address: str,
        wallet_name: Optional[str],
        wallet_category: str,
        chain: str,
        value_eth: float,
        description: str,
        timestamp: Optional[datetime] = None,
        related_transactions: Optional[List[str]] = None,
        confidence: float = 0.0,
        tags: Optional[List[str]] = None,
        provenance: Optional[Dict[str, Any]] = None,
    ):
        self.signal_type = signal_type
        self.strength = strength
        self.transaction_hash = transaction_hash
        self.wallet_address = wallet_address
        self.wallet_name = wallet_name
        self.wallet_category = wallet_category
        self.chain = chain
        self.value_eth = value_eth
        self.description = description
        self.timestamp = timestamp or datetime.now()
        self.related_transactions = related_transactions or []
        self.confidence = confidence
        self.tags = tags or []
        self.provenance = deepcopy(provenance or {})
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert signal to dictionary format"""
        return {
            "signal_type": self.signal_type.value,
            "strength": self.strength.value,
            "transaction_hash": self.transaction_hash,
            "wallet_address": self.wallet_address,
            "wallet_name": self.wallet_name,
            "wallet_category": self.wallet_category,
            "chain": self.chain,
            "value_eth": self.value_eth,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "related_transactions": self.related_transactions,
            "confidence": self.confidence,
            "tags": self.tags,
            "provenance": deepcopy(self.provenance)
        }
    
    def get_message(self) -> str:
        """Get formatted message for notification (Telegram HTML parse mode)."""
        chain_name = (self.chain or "").capitalize()
        signal_emoji = self._get_signal_emoji()
        strength_stars = self._get_strength_stars()

        # Wallet line: link to explorer when chain is known, else short hash text.
        wallet_label = self.wallet_name or f"{self.wallet_address[:8]}…{self.wallet_address[-6:]}"
        wallet_url = explorer_address_url(self.chain, self.wallet_address)
        if wallet_url:
            wallet_html = f'<a href="{wallet_url}">{html.escape(wallet_label)}</a>'
        else:
            wallet_html = html.escape(wallet_label)

        # Transaction line: tappable "View transaction" link when chain is known.
        tx_url = explorer_tx_url(self.chain, self.transaction_hash)
        tx_short = f"{self.transaction_hash[:8]}…{self.transaction_hash[-6:]}"
        if tx_url:
            tx_html = f'<a href="{tx_url}">View transaction ↗</a>'
        else:
            tx_html = html.escape(tx_short)

        time_str = self.timestamp.strftime("%Y-%m-%d %H:%M")

        message = (
            f"{signal_emoji} <b>{self.signal_type.value.upper().replace('_', ' ')} SIGNAL</b> {strength_stars}\n\n"
            f"{html.escape(self.description)}\n\n"
            f"💰 <b>{self.value_eth:.2f} ETH</b>\n"
            f"👛 {wallet_html} · {html.escape(self.wallet_category)}\n"
            f"⛓️ {chain_name} · 🕐 {time_str}\n"
            f"🔗 {tx_html}\n"
        )

        return message
    
    def _get_signal_emoji(self) -> str:
        """Get emoji for signal type"""
        emoji_map = {
            SignalType.ACCUMULATION: "🟢",
            SignalType.DISTRIBUTION: "🔴",
            SignalType.TRANSFER: "↔️",
            SignalType.EXCHANGE_DEPOSIT: "📤",
            SignalType.EXCHANGE_WITHDRAWAL: "📥",
            SignalType.UNUSUAL_ACTIVITY: "⚠️"
        }
        return emoji_map.get(self.signal_type, "📊")
    
    def _get_strength_stars(self) -> str:
        """Get star rating based on signal strength"""
        star_map = {
            SignalStrength.LOW: "⭐",
            SignalStrength.MEDIUM: "⭐⭐",
            SignalStrength.HIGH: "⭐⭐⭐",
            SignalStrength.VERY_HIGH: "⭐⭐⭐⭐"
        }
        return star_map.get(self.strength, "")


class SignalGenerator:
    """Generator for creating trading signals from analyzed transactions"""
    
    def __init__(self):
        """Initialize signal generator"""
        pass
    
    def generate_signals(self, analyzed_transaction: Dict[str, Any]) -> List[Signal]:
        """
        Generate signals from an analyzed transaction
        
        Args:
            analyzed_transaction: Transaction with analysis data
            
        Returns:
            List of generated signals
        """
        signals = []
        
        # Skip if not significant
        if not analyzed_transaction.get("is_significant", False):
            return signals
        

        direction = analyzed_transaction.get("direction")
        value_eth = analyzed_transaction.get("value_eth", 0)


        
        # Get wallet info based on direction
        if direction == TransactionDirection.OUTGOING.value:
            wallet_info = analyzed_transaction.get("from_wallet_info", {})
            to_address = analyzed_transaction.get("to", "unknown")
            
            # Check if this is a deposit to exchange
            if to_address in self._get_exchange_addresses():
                signal = self._create_exchange_deposit_signal(
                    analyzed_transaction, wallet_info, to_address
                )
                signals.append(signal)
            
            # General distribution signal
            elif value_eth >= 100:  # Higher threshold for distribution
                signal = self._create_distribution_signal(
                    analyzed_transaction, wallet_info
                )
                signals.append(signal)
                
        elif direction == TransactionDirection.INCOMING.value:
            wallet_info = analyzed_transaction.get("to_wallet_info", {})
            from_address = analyzed_transaction.get("from", "unknown")
            
            # Check if this is a withdrawal from exchange
            if from_address in self._get_exchange_addresses():
                signal = self._create_exchange_withdrawal_signal(
                    analyzed_transaction, wallet_info, from_address
                )
                signals.append(signal)
            
            # General accumulation signal
            elif value_eth >= 50:  # Lower threshold for accumulation
                signal = self._create_accumulation_signal(
                    analyzed_transaction, wallet_info
                )
                signals.append(signal)
                
        elif direction == TransactionDirection.INTERNAL.value:
            # Transfer between tracked wallets
            from_wallet = analyzed_transaction.get("from_wallet_info", {})
            to_wallet = analyzed_transaction.get("to_wallet_info", {})
            
            signal = self._create_internal_transfer_signal(
                analyzed_transaction, from_wallet, to_wallet
            )
            signals.append(signal)
        
        return signals
    
    def _create_accumulation_signal(
        self, 
        transaction: Dict[str, Any], 
        wallet_info: Dict[str, Any]
    ) -> Signal:
        """Create an accumulation signal"""
        value_eth = transaction.get("value_eth", 0)
        strength = self._determine_signal_strength(value_eth)
        
        description = f"Whale wallet accumulating {value_eth:.2f} ETH"
        
        return Signal(
            signal_type=SignalType.ACCUMULATION,
            strength=strength,
            transaction_hash=transaction.get("hash", "unknown"),
            wallet_address=wallet_info.get("address", "unknown"),
            wallet_name=wallet_info.get("name"),
            wallet_category=wallet_info.get("category", "unknown"),
            chain=transaction.get("chain", "ethereum"),
            value_eth=value_eth,
            description=description,
            confidence=transaction.get("type_confidence", 0.5),
            tags=wallet_info.get("tags", [])
        )
    
    def _create_distribution_signal(
        self, 
        transaction: Dict[str, Any], 
        wallet_info: Dict[str, Any]
    ) -> Signal:
        """Create a distribution signal"""
        value_eth = transaction.get("value_eth", 0)
        strength = self._determine_signal_strength(value_eth)
        
        description = f"Whale wallet distributing {value_eth:.2f} ETH"
        
        return Signal(
            signal_type=SignalType.DISTRIBUTION,
            strength=strength,
            transaction_hash=transaction.get("hash", "unknown"),
            wallet_address=wallet_info.get("address", "unknown"),
            wallet_name=wallet_info.get("name"),
            wallet_category=wallet_info.get("category", "unknown"),
            chain=transaction.get("chain", "ethereum"),
            value_eth=value_eth,
            description=description,
            confidence=transaction.get("type_confidence", 0.5),
            tags=wallet_info.get("tags", [])
        )
    
    def _create_exchange_deposit_signal(
        self, 
        transaction: Dict[str, Any], 
        wallet_info: Dict[str, Any],
        exchange_address: str
    ) -> Signal:
        """Create an exchange deposit signal"""
        value_eth = transaction.get("value_eth", 0)
        strength = self._determine_signal_strength(value_eth)
        exchange_name = self._get_exchange_name(exchange_address)
        
        description = f"Whale depositing {value_eth:.2f} ETH to {exchange_name}"
        
        return Signal(
            signal_type=SignalType.EXCHANGE_DEPOSIT,
            strength=strength,
            transaction_hash=transaction.get("hash", "unknown"),
            wallet_address=wallet_info.get("address", "unknown"),
            wallet_name=wallet_info.get("name"),
            wallet_category=wallet_info.get("category", "unknown"),
            chain=transaction.get("chain", "ethereum"),
            value_eth=value_eth,
            description=description,
            confidence=transaction.get("type_confidence", 0.5),
            tags=wallet_info.get("tags", []) + ["exchange", exchange_name.lower()]
        )
    
    def _create_exchange_withdrawal_signal(
        self, 
        transaction: Dict[str, Any], 
        wallet_info: Dict[str, Any],
        exchange_address: str
    ) -> Signal:
        """Create an exchange withdrawal signal"""
        value_eth = transaction.get("value_eth", 0)
        strength = self._determine_signal_strength(value_eth)
        exchange_name = self._get_exchange_name(exchange_address)
        
        description = f"Whale withdrawing {value_eth:.2f} ETH from {exchange_name}"
        
        return Signal(
            signal_type=SignalType.EXCHANGE_WITHDRAWAL,
            strength=strength,
            transaction_hash=transaction.get("hash", "unknown"),
            wallet_address=wallet_info.get("address", "unknown"),
            wallet_name=wallet_info.get("name"),
            wallet_category=wallet_info.get("category", "unknown"),
            chain=transaction.get("chain", "ethereum"),
            value_eth=value_eth,
            description=description,
            confidence=transaction.get("type_confidence", 0.5),
            tags=wallet_info.get("tags", []) + ["exchange", exchange_name.lower()]
        )
    
    def _create_internal_transfer_signal(
        self, 
        transaction: Dict[str, Any], 
        from_wallet: Dict[str, Any],
        to_wallet: Dict[str, Any]
    ) -> Signal:
        """Create an internal transfer signal (between tracked wallets)"""
        value_eth = transaction.get("value_eth", 0)
        strength = self._determine_signal_strength(value_eth)
        
        from_name = from_wallet.get("name") or from_wallet.get("address", "unknown")[:8]
        to_name = to_wallet.get("name") or to_wallet.get("address", "unknown")[:8]
        
        description = f"Whale transfer: {from_name} -> {to_name} ({value_eth:.2f} ETH)"
        
        return Signal(
            signal_type=SignalType.TRANSFER,
            strength=strength,
            transaction_hash=transaction.get("hash", "unknown"),
            wallet_address=from_wallet.get("address", "unknown"),
            wallet_name=from_wallet.get("name"),
            wallet_category=from_wallet.get("category", "unknown"),
            chain=transaction.get("chain", "ethereum"),
            value_eth=value_eth,
            description=description,
            confidence=transaction.get("type_confidence", 0.5),
            tags=from_wallet.get("tags", []) + to_wallet.get("tags", [])
        )
    
    def _determine_signal_strength(self, value_eth: float) -> SignalStrength:
        """Determine signal strength based on transaction value"""
        if value_eth >= 1000:
            return SignalStrength.VERY_HIGH
        elif value_eth >= 500:
            return SignalStrength.HIGH
        elif value_eth >= 200:
            return SignalStrength.MEDIUM
        else:
            return SignalStrength.LOW
    
    def _get_exchange_addresses(self) -> List[str]:
        """Lista indirizzi CEX — da src/config/known_addresses.py (fonte unica)."""
        return CEX_ADDRESS_LIST

    def _get_exchange_name(self, address: str) -> str:
        """Nome exchange da indirizzo — da src/config/known_addresses.py (fonte unica)."""
        return get_exchange_name(address)
