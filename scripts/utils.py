from brownie import web3
from datetime import datetime
from collections import Counter
from tqdm import tqdm, trange
from toolz import valfilter
from eth_abi import decode_single
import requests
import pytz
import json
from web3.exceptions import BadFunctionCallOutput


class MerkleTree:
    def __init__(self, elements):
        self.elements = sorted(set(web3.keccak(hexstr=el) for el in elements))
        self.layers = MerkleTree.get_layers(self.elements)

    @property
    def root(self):
        return self.layers[-1][0]

    def get_proof(self, el):
        el = web3.keccak(hexstr=el)
        idx = self.elements.index(el)
        proof = []
        for layer in self.layers:
            pair_idx = idx + 1 if idx % 2 == 0 else idx - 1
            if pair_idx < len(layer):
                proof.append(encode_hex(layer[pair_idx]))
            idx //= 2
        return proof

    @staticmethod
    def get_layers(elements):
        layers = [elements]
        while len(layers[-1]) > 1:
            layers.append(MerkleTree.get_next_layer(layers[-1]))
        return layers

    @staticmethod
    def get_next_layer(elements):
        return [MerkleTree.combined_hash(a, b) for a, b in zip_longest(elements[::2], elements[1::2])]

    @staticmethod
    def combined_hash(a, b):
        if a is None:
            return b
        if b is None:
            return a
        return web3.keccak(b''.join(sorted([a, b])))


def getProposalsListUrl(item):
    key = item.get('key')
    return f'https://hub.snapshot.page/api/{key}/proposals'

def getProposalUrl(snapshot_id, item):
    key = item.get('key')
    return f'https://hub.snapshot.page/api/{key}/proposal/{snapshot_id}'

def getPage(url):
    return requests.get(url).json()

def timestamp_to_datetime(timestamp):
    return datetime.fromtimestamp(int(timestamp), pytz.UTC)

def getFunctionSignature(tx_input):
    '''
        takes tx input data and returns the hex signature
    '''
    if type(tx_input) == str:
        return tx_input[:10]
    elif type(tx_input) == bytes:
        return '0x' + tx_input[:4].hex()
    return tx_input[:4].hex()

def getArgsFromDefinition(definition):
    '''
        takes function definition and returns the list of args of the function
    '''
    return f'({"".join(definition.split("(")[1:])}'

def strToFunctionSignature(definition):
    '''
        takes function definition and computes the hex signature
    '''
    return web3.keccak(text=definition)[:4].hex()


def LoadJson(fn):
    with open(fn, 'r') as fp:
        return json.load(fp)
   

def WriteJson(fn, data):
    with open(fn, 'w') as fp:
        json.dump(data, fp)


def processCounter(counter):
    filteredFinal = valfilter(bool, dict(counter.most_common()))
    return filteredFinal   

def isContract(address):
    return web3.eth.getCode(address).hex() != '0x'






class SnapShotScraper:
    def __init__(self, key, cutoff, participants=None, debug=False):
        self.key = key
        self.cutoff = cutoff
        self.participants = participants if participants else Counter()
        self.debug = debug
    
    def getProposalsListUrl(self):
        return f'https://hub.snapshot.page/api/{self.key}/proposals'    

    def getProposalUrl(self, snapshot_id):
        return f'https://hub.snapshot.page/api/{self.key}/proposal/{snapshot_id}'

    def scrape(self):
        proposals = getPage(self.getProposalsListUrl())
        for snapshot_id, value in tqdm(proposals.items()):
            timestamp = timestamp_to_datetime(value['msg']['timestamp'])
            if timestamp > self.cutoff: 
                if self.debug:
                    print(f"{snapshot_id} {timestamp} is after cutoff date of {self.cutoff}")
                continue         
            proposal_submitter_address = value['address']
            if self.debug:
                print(f"Proposal {snapshot_id} on {timestamp} by {proposal_submitter_address}")
            # does paticipant get credited twice? for propsing and voting?
            self.participants[proposal_submitter_address] += 1
            votes = getPage(self.getProposalUrl(snapshot_id))
            for voter in votes.keys():
                self.participants[voter] += 1
        return self.participants


class ContractLogParser:
    def __init__(self, startBlock, endBlock, address, abi_fn, event_name, use_amount_as_airdrop=False, chunk_amount=1000):
        self.startBlock = startBlock
        self.endBlock = endBlock
        self.address = address
        self.chunk_amount = chunk_amount
        self.contract = web3.eth.contract(address, abi=LoadJson(abi_fn))
        self.event = getattr(self.contract.events, event_name)
        self.use_amount_as_airdrop = use_amount_as_airdrop


    def get_logs(self, argument_filters=None):
        for start in trange(self.startBlock, self.endBlock, self.chunk_amount):
            end = min(start + 999, self.endBlock)
            # logs = self.event().getLogs(fromBlock=start, toBlock=end)
            logs = self.event().getLogs(fromBlock=start, toBlock=end, argument_filters=argument_filters)
            for log in logs:
                yield log



class TxDataParser:
    def __init__(self, definition, names, want_fields, is_meta_transaction=False, use_sender_address=False):
        self.definition = definition
        self.names = names 
        self.want_fields = want_fields
        self.args = getArgsFromDefinition(definition)
        self.signature = strToFunctionSignature(definition)
        self.is_meta_transaction = is_meta_transaction
        self.use_sender_address = use_sender_address

    def parse_tx(self, tx_data):
        if type(tx_data) == str:
            tx_data = bytes.fromhex(tx_data[10:])
        elif type(tx_data) == bytes:
            tx_data = tx_data[4:]            
        result = decode_single(self.args, tx_data)
        result = dict(zip(self.names, result))
        return [result.get(want) for want in self.want_fields]


ARGENT = TxDataParser(
                    definition='execute(address,bytes,uint256,bytes,uint256,uint256)', 
                    names=('_wallet', '_data', '_nonce', '_signatures', '_gasPrice', '_gasLimit'),
                    want_fields=('_wallet', '_data'))


PARSERS = {
    '0x77f61403': TxDataParser(
                    definition='mint(string,address,uint256,bytes32,bytes)', 
                    names=('_symbol', '_recipient', '_amount', '_nHash', '_sig'),
                    want_fields=('_recipient','_amount')),
    '0xd039fca1': TxDataParser(
                    definition='executeMetaTransaction(address,bytes,string,string,bytes32,bytes32,uint8)', 
                    names=('userAddress', 'functionSignature', 'message', 'length', 'sigR', 'sigS', 'sigV'),
                    want_fields=('userAddress', 'functionSignature'),
                    is_meta_transaction=True),
    '0x29349116': TxDataParser(
                    definition='mintThenSwap(uint256,uint256,uint256,int128,address,uint256,bytes32,bytes)',
                    names=('_minExchangeRate','_newMinExchangeRate','_slippage','_j','_coinDestination','_amount','_nHash','_sig'),
                    want_fields=('_coinDestination', '_amount')),
    '0xa318f9de': TxDataParser(
                    definition='mintThenDeposit(address,uint256,uint256[3],uint256,uint256,bytes32,bytes)',
                    names=('_wbtcDestination', '_amount', '_amounts', '_min_mint_amount', '_new_min_mint_amount', '_nHash', '_sig'),
                    want_fields=('_wbtcDestination', '_amount')),
    '0x74955c42': TxDataParser(
                    definition='mintThenSwap(uint256,uint256,uint256,address,uint256,bytes32,bytes)',
                    names=('_minExchangeRate', '_newMinExchangeRate', '_slippage', '_wbtcDestination', '_amount', '_nHash', '_sig'),
                    want_fields=('_wbtcDestination', '_amount')),
    '0xdcf0bb3a': TxDataParser(
                    definition='mintThenDeposit(address,uint256,uint256[2],uint256,uint256,bytes32,bytes)',
                    names=('_wbtcDestination', '_amount', '_amounts', '_min_mint_amount', '_new_min_mint_amount', '_nHash', '_sig'),
                    want_fields=('_wbtcDestination', '_amount')),
    '0x0bfe8b92': TxDataParser(
                    definition='recoverStuck(bytes,uint256,bytes32,bytes)',
                    names=('encoded', '_amount', '_nHash', '_sig'),
                    want_fields=('encoded', '_amount'), # returning encoded field as placeholder to be replaced with sender address
                    use_sender_address=True),
    '0x834a7182': TxDataParser(
                    definition='mintThenSwap(uint256,address,uint256,bytes32,bytes)',
                    names=('_minWbtcAmount', '_wbtcDestination', '_amount', '_nHash', '_sig'),
                    want_fields=('_wbtcDestination', '_amount')),
    '0x47f701e7': TxDataParser(
                    definition='mintRenBTC(address,uint256,uint256,uint256,bytes32,bytes)',
                    names=('_recipient', '_gasFee', '_serviceFeeRate', '_amount', '_nHash', '_sig'),
                    want_fields=('_recipient', '_amount')),
    '0x0f5b02cd': TxDataParser(
                    definition='mintDai(uint256,bytes,uint256,uint256,bytes32,bytes)',
                    names=('_dart', '_btcAddr', '_minWbtcAmount', '_amount', '_nHash', '_sig'),
                    want_fields=('_btcAddr', '_amount'),# returning _btcAddr field as placeholder to be replaced with sender address
                    use_sender_address=True),
    '0x2012aca7': TxDataParser(
                    definition='deposit(bytes,uint256,bytes32,bytes)',
                    names=('_msg', '_amount', '_nHash', '_sig'),
                    want_fields=('_msg', '_amount'),# returning _msg field as placeholder to be replaced with sender address
                    use_sender_address=True),
    '0xec369f7d': TxDataParser(
                    definition='depositbtc(address,bytes,uint256,bytes32,bytes)',
                    names=('_user', '_msg', '_amount', '_nHash', '_sig'),
                    want_fields=('_user', '_amount')),
    '0xaacaaf88': TxDataParser(
                    definition='execute(address,bytes,uint256,bytes,uint256,uint256)', 
                    names=('_wallet', '_data', '_nonce', '_signatures', '_gasPrice', '_gasLimit'),
                    want_fields=('_wallet', '_data')),
    '0xe0e90acf': TxDataParser(
                    definition='cast(address[],bytes[],address)',
                    names=('_targets', '_datas', '_origin'),
                    want_fields=('_origin', '_datas')),  
    '0xb5090bdc': TxDataParser(
                    definition='ZapIn(address,address,address,uint256,uint256)',
                    names=('_toWhomToIssue', '_IncomingTokenAddress', '_curvePoolExchangeAddress','_IncomingTokenQty','_minPoolTokens'),
                    want_fields=('_toWhomToIssue', '_minPoolTokens')), 
    '0xd1bd8205': TxDataParser(
                    definition='ZapIn(address,address,uint16,address,uint256,uint256)',
                    names=('_toWhomToIssue', '_toYVaultAddress', '_vaultType','_fromTokenAddress','_amount'),
                    want_fields=('_toWhomToIssue', '_amount')),
    '0xc1169548': TxDataParser(
                    definition='execute(address,address,bytes,uint256,bytes,uint256,uint256,address,address)',
                    names=('_wallet', '_feature', '_data', '_nonce', '_signatures', '_gasPrice', '_gasLimit', '_refundToken', '_refundAddress'),
                    want_fields=('_wallet', '_data')),
    '0x1d572320': TxDataParser(
                    definition='ZapIn(address,address,address,address,uint256,uint256)',
                    names=('_toWhomToIssue', '_FromTokenContractAddress', '_ToUnipoolToken0','_ToUnipoolToken1','_amount'),
                    want_fields=('_toWhomToIssue', '_amount')),
    '0xdb23d9f4': TxDataParser(
                    definition='V1_to_V2_Pipe(address,uint256,address,address)',
                    names=('fromTokenAddress', 'uniV1Amount', 'toTokenAddress','toWhomToIssue'),
                    want_fields=('toWhomToIssue', 'uniV1Amount')),

}


def getMintersInfo(tx, second_pass=False):
    signature = getFunctionSignature(tx['input'])
    parser = PARSERS.get(signature, None)
    if parser is None:
        #print(f"No Match for tx signature {tx['hash'].hex()}")
        return None 
    want_fields = parser.parse_tx(tx['input'])
    if parser.is_meta_transaction and second_pass == False:
        user_address, tx_data = want_fields
        tx_copy = tx.__dict__.copy()
        tx_copy['input'] = tx_data
        return getMintersInfo(tx_copy, second_pass=True)
    if parser.use_sender_address:
        want_fields[0] = tx.get("from")
    user_address, amount = want_fields
    return (user_address, amount)

def processBalancePoolJoin(log):
    try:
        address = getDSProxyOwner(log.args.caller)
        #print(f"{address} added {log.args.tokenAmountIn/1e8}")
        return (address, log.args.tokenAmountIn)
    except ValueError:
        tx = web3.eth.getTransaction(log.transactionHash.hex())
        sig = getFunctionSignature(tx.input)
        if sig == '0xaacaaf88':
            wallet, _ = ARGENT.parse_tx(tx.input)
            # print(f"{log.args.caller} {wallet} {log.args.tokenAmountIn}")
            return (wallet, log.args.tokenAmountIn)
        return None  
    except BadFunctionCallOutput:
        if isContract(log.args.caller) == False:
            #print(f"{log.args.caller} not a contract")
            return (log.args.caller, log.args.tokenAmountIn)
        else:
            print(f"{log.args.caller} has error from tx {log.transactionHash.hex()} BadFunctionCallOutput") 
            return None
    except Exception as e:
        #here is 
        print(f"{log.args.caller} has error from tx {log.transactionHash.hex()} of {e}")    
        #errors3.append([x.args.caller,x.transactionHash.hex()])    
        return None

#def 