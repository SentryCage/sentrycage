"""
Blockchain client for interacting with Ethereum and Arbitrum networks
"""
from typing import Dict, List, Any, Optional
import logging
import time

from web3 import Web3
from web3.exceptions import BlockNotFound, TransactionNotFound

from src.config.settings import ETHEREUM_RPC_URL, ARBITRUM_RPC_URL
from src.config.wallet_registry import Chain

# Configure logger
logger = logging.getLogger(__name__)

class BlockchainClient:
    """Client for interacting with blockchain networks"""
    
    def __init__(self, settings=None):
        """Initialize blockchain connections"""
        urls = settings.rpc_urls if settings else {
            Chain.ETHEREUM: ETHEREUM_RPC_URL, Chain.ARBITRUM: ARBITRUM_RPC_URL}
        timeout = settings.request_timeout if settings else 10
        self._clients = {chain: Web3(Web3.HTTPProvider(url, request_kwargs={'timeout': timeout}))
                         for chain, url in urls.items() if url}
        self._last_blocks = {}
        self._available_chains = set()

    def _validate_connections(self) -> None:
        """Valida connessioni RPC e degrada gracefully se non disponibili.

        Non solleva eccezioni: permette all'app di avviarsi usando solo
        Explorer API (Etherscan) anche senza nodi RPC configurati.
        """
        for chain, web3 in self._clients.items():
            if web3.is_connected():
                logger.info(f"Connected to {chain.value}: {web3.client_version}")
                self._available_chains.add(chain)
            else:
                logger.warning(
                    f"Cannot connect to {chain.value} RPC node — "
                    f"RPC-based methods disabled for this chain. "
                    f"Check {chain.value.upper()}_RPC_URL in .env"
                )

    def is_chain_available(self, chain: Chain) -> bool:
        """Verifica se la connessione RPC per la chain è attiva."""
        return chain in self._available_chains
    
    def get_chain_id(self, chain: Chain) -> int:
        return self._clients[chain].eth.chain_id

    def get_latest_block_number(self, chain: Chain) -> int:
        """
        Get the latest block number for the specified chain
        
        Args:
            chain: The blockchain to query
            
        Returns:
            Latest block number
        """
        return self._clients[chain].eth.block_number
    
    def get_transaction(self, tx_hash: str, chain: Chain) -> Dict[str, Any]:
        """
        Get transaction details by hash
        
        Args:
            tx_hash: Transaction hash
            chain: The blockchain to query
            
        Returns:
            Transaction details
        """
        web3 = self._clients[chain]
        try:
            tx = web3.eth.get_transaction(tx_hash)
            tx_receipt = web3.eth.get_transaction_receipt(tx_hash)
            
            # Combine transaction and receipt data
            result = dict(tx)
            result.update({
                'status': tx_receipt.get('status'),
                'gasUsed': tx_receipt.get('gasUsed'),
                'logs': tx_receipt.get('logs', []),
                'contractAddress': tx_receipt.get('contractAddress')
            })
            
            return result
        except TransactionNotFound:
            logger.warning(f"Transaction {tx_hash} not found on {chain.value}")
            return {}
    
    def get_wallet_transactions(
        self, 
        address: str, 
        chain: Chain, 
        from_block: Optional[int] = None, 
        to_block: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get transactions for a wallet address
        
        Note: This is a basic implementation that scans blocks.
        In production, this would use more efficient methods like
        API services or event filters.
        
        Args:
            address: Wallet address to check
            chain: The blockchain to query
            from_block: Starting block (defaults to latest-100)
            to_block: Ending block (defaults to latest)
            
        Returns:
            List of transactions
        """
        web3 = self._clients[chain]
        address = web3.to_checksum_address(address)
        
        # Default to latest block if not specified
        if to_block is None:
            to_block = web3.eth.block_number
        
        # Default to 100 blocks back if not specified
        if from_block is None:
            from_block = max(to_block - 100, 0)
        
        logger.info(f"Scanning blocks {from_block} to {to_block} for {address} on {chain.value}")
        
        transactions = []
        
        # This is inefficient for production - would use APIs or indexers instead
        for block_num in range(from_block, to_block + 1):
            try:
                block = web3.eth.get_block(block_num, full_transactions=True)
                
                for tx in block.transactions:
                    # Check if the transaction involves our address
                    if (tx.get('from', '').lower() == address.lower() or 
                        tx.get('to', '').lower() == address.lower()):
                        
                        # Add chain information to transaction
                        tx_data = dict(tx)
                        tx_data['chain'] = chain.value
                        tx_data['blockTimestamp'] = block.timestamp
                        
                        transactions.append(tx_data)
                
            except BlockNotFound:
                logger.warning(f"Block {block_num} not found on {chain.value}")
                continue
            
            # Add a small delay to avoid overwhelming the node
            time.sleep(0.05)
        
        return transactions
