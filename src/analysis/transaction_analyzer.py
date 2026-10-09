"""
Transaction analyzer for processing and categorizing blockchain transactions
"""
from enum import Enum
from typing import Dict, Any, Optional, Tuple
import logging
from web3 import Web3

from src.config.settings import TRANSACTION_THRESHOLD
from src.config.wallet_registry import get_wallet_by_address, WalletInfo
from src.config.known_addresses import CEX_ADDRESSES

# Configure logger
logger = logging.getLogger(__name__)

class TransactionType(Enum):
    """Types of blockchain transactions"""
    TRANSFER = "transfer"
    SWAP = "swap"
    LIQUIDITY = "liquidity"
    STAKE = "stake"
    CONTRACT_INTERACTION = "contract_interaction"
    UNKNOWN = "unknown"

class TransactionDirection(Enum):
    """Direction of fund flow"""
    INCOMING = "incoming"
    OUTGOING = "outgoing"
    INTERNAL = "internal"

class TransactionAnalyzer:
    """Analyzer for blockchain transactions"""
    
    # Common DEX router addresses
    DEX_ROUTERS = {
        # Ethereum
        "0x7a250d5630b4cf539739df2c5dacb4c659f2488d": ("Uniswap V2", "ethereum"),
        "0xe592427a0aece92de3edee1f18e0157c05861564": ("Uniswap V3", "ethereum"),
        "0x1111111254fb6c44bac0bed2854e76f90643097d": ("1inch", "ethereum"),
        # Arbitrum
        "0x1b02da8cb0d097eb8d57a175b88c7d8b47997506": ("SushiSwap", "arbitrum"),
        "0xef1c6e67703c7bd7107eed8303fbe6ec2554bf6b": ("Uniswap V3", "arbitrum"),
    }
    
    # Indirizzi CEX — fonte di verità in src/config/known_addresses.py
    CEX_ADDRESSES = CEX_ADDRESSES
    
    def __init__(self, threshold=TRANSACTION_THRESHOLD, registry=None):
        """Initialize the transaction analyzer"""
        self.threshold = threshold
        self.registry = registry
        self.w3 = Web3()  # Used for utility functions
    
    def analyze_transaction(self, transaction: Dict[str, Any]) -> Dict[str, Any]:
        """
        Analyze a blockchain transaction
        
        Args:
            transaction: Transaction data from blockchain or explorer
            
        Returns:
            Enriched transaction data with analysis
        """
        result = transaction.copy()
        
        # Basic validation
        if not transaction:
            return {"error": "Empty transaction data"}
        
        # Calculate value in ETH
        if "value" in transaction:
            value_eth = float(self.w3.from_wei(int(transaction["value"]), "ether"))
            result["value_eth"] = value_eth
        
        # Determine transaction type
        tx_type, confidence = self._determine_transaction_type(transaction)
        result["transaction_type"] = tx_type.value
        result["type_confidence"] = confidence
        
        # Determine direction
        from_addr = (transaction.get("from") or "").lower()
        to_addr = (transaction.get("to") or "").lower()
        
        # Get wallet info if available
        chain = transaction.get('chain', 'ethereum')
        from_wallet = self._lookup(from_addr, chain)
        to_wallet = self._lookup(to_addr, chain)
        
        result["from_wallet_info"] = self._format_wallet_info(from_wallet) if from_wallet else None
        result["to_wallet_info"] = self._format_wallet_info(to_wallet) if to_wallet else None
        
        # Determine if this is a significant transaction
        threshold = self._get_threshold(from_wallet, to_wallet)
        result["is_significant"] = result.get("value_eth", 0) >= threshold and str(transaction.get("isError", "0")) == "0"
        result["threshold_used"] = threshold
        
        # Add direction context
        if from_wallet and to_wallet:
            direction = TransactionDirection.INTERNAL
        elif from_wallet:
            direction = TransactionDirection.OUTGOING
        elif to_wallet:
            direction = TransactionDirection.INCOMING
        else:
            # Neither from nor to address is in our registry
            direction = None
            
        result["direction"] = direction.value if direction else None
        
        return result

    def _lookup(self, address, chain):
        if self.registry is not None:
            return self.registry.lookup(address, chain)
        wallet = get_wallet_by_address(address)
        return wallet if wallet and any(network.value == chain for network in wallet.chains) else None
    
    def _determine_transaction_type(self, transaction: Dict[str, Any]) -> Tuple[TransactionType, float]:
        """
        Determine the type of transaction
        
        Args:
            transaction: Transaction data
            
        Returns:
            Tuple of (TransactionType, confidence score)
        """
        to_addr = (transaction.get("to") or "").lower()
        input_data = transaction.get("input", "0x")
        
        # Simple ETH transfer
        if input_data == "0x" or input_data == "0x0":
            return TransactionType.TRANSFER, 0.9
        
        # Check for known DEX routers
        if to_addr in self.DEX_ROUTERS:
            return TransactionType.SWAP, 0.85
            
        # Check for known CEX addresses
        if to_addr in self.CEX_ADDRESSES:
            return TransactionType.TRANSFER, 0.85
            
        # Check for common staking methods
        if input_data.startswith("0xa694fc3a") or input_data.startswith("0xdf8de3e7"):
            return TransactionType.STAKE, 0.75
            
        # Check for liquidity provision
        if input_data.startswith("0xe8e33700") or input_data.startswith("0xf305d719"):
            return TransactionType.LIQUIDITY, 0.75
            
        # Default to generic contract interaction
        if len(input_data) > 10:
            return TransactionType.CONTRACT_INTERACTION, 0.6
            
        return TransactionType.UNKNOWN, 0.3
    
    def _format_wallet_info(self, wallet: WalletInfo) -> Dict[str, Any]:
        """Format wallet information for the analysis result"""
        return {
            "address": wallet.address,
            "name": wallet.name,
            "category": wallet.category.value,
            "tags": wallet.tags
        }
    
    def _get_threshold(self, from_wallet: Optional[WalletInfo], to_wallet: Optional[WalletInfo]) -> float:
        """
        Get the significance threshold to use for this transaction
        
        Args:
            from_wallet: Sender wallet info if available
            to_wallet: Recipient wallet info if available
            
        Returns:
            Threshold value in ETH
        """
        # Use wallet-specific threshold if available
        if from_wallet and from_wallet.threshold_override is not None:
            return from_wallet.threshold_override
            
        if to_wallet and to_wallet.threshold_override is not None:
            return to_wallet.threshold_override
            
        # Use default threshold
        return self.threshold
