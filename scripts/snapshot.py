from brownie import web3, network, ZERO_ADDRESS
from brownie import Wei, accounts, interface, rpc
from collections import Counter, defaultdict
from tqdm import tqdm, trange
from datetime import datetime
import pytz
import json
from .utils import processCounter, SnapShotScraper, WriteJson, LoadJson, ContractLogParser
from .utils import getMintersInfo, isContract, MerkleTree
from .constants import ZERO_ADDRESS, SKIP_ADDRESSES, CURVE_ADAPTERS, INSTACCOUNT, ARGENT, ZAPPER, UNI_UNDECODABLE, ARGENT_UNISWAP, ZERION

import os
import math
import toml
from concurrent.futures import ThreadPoolExecutor
from fractions import Fraction
from functools import partial, wraps
from itertools import zip_longest
from pathlib import Path
from scripts.smooth import smooth

from eth_abi import decode_single, encode_single
from eth_abi.packed import encode_abi_packed
from eth_utils import encode_hex
from toolz import valfilter, valmap
from click import secho
import sys
import csv

DISTRIBUTOR_ADDRESS = '0x5e37996bcfF8C169e77b00D7b6e7261bbC60761e'



def cached(path):
    path = Path(path)
    codec = {'.toml': toml, '.json': json}[path.suffix]
    codec_args = {'.json': {'indent': 2}}.get(path.suffix, {})

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if path.exists():
                print('load from cache', path)
                return codec.loads(path.read_text())
            else:
                result = func(*args, **kwargs)
                os.makedirs(path.parent, exist_ok=True)
                path.write_text(codec.dumps(result, **codec_args))
                print('write to cache', path)
                return result

        return wrapper

    return decorator


def get_yearn_governance(out_file_name=None):
    YFI = SnapShotScraper(
        key = 'yearn',
        cutoff = datetime(2020, 11, 13, tzinfo=pytz.UTC),
        debug=False
      )
    participants = YFI.scrape()
    if out_file_name != None:
        WriteJson(out_file_name, participants)     
    return participants


def get_ygov_and_snapshot_participants(out_file_name=None):
    # users = Counter()
    users = get_yearn_governance()
    # https://etherscan.io/tx/0xc07668652a1a2123e6dbc69785dfdee2b5e58f48c08751af0d140b7415a9f4db
    START_BLOCK= 10553531 
    SNAPSHOT_BLOCK = 11245937  # Nov-13-2020 12:00:12 AM +UTC
    ygovAddress = '0xBa37B002AbaFDd8E89a1995dA52740bbC013D992'
    ygovABI = LoadJson(f"./interfaces/Yearn.json")
    yearn = web3.eth.contract(ygovAddress, abi=ygovABI)
    for start in trange(START_BLOCK, SNAPSHOT_BLOCK, 1000):
        end = min(start + 999, SNAPSHOT_BLOCK)
        logs = yearn.events.NewProposal().getLogs(fromBlock=start, toBlock=end)
        for log in logs:
            users[log.args.creator] = 1

        logs = yearn.events.Staked().getLogs(fromBlock=start, toBlock=end)
        for log in logs:
            # users[log.args.user] = log.args.amount
            users[log.args.user] = 1

        logs = yearn.events.Vote().getLogs(fromBlock=start, toBlock=end)
        for log in logs:
            users[log.args.voter] = 1

    result = processCounter(users)
    if out_file_name != None:
        WriteJson(out_file_name, result)
    return result 

def cleanupSnapshot(new_snapshot, old_fn):
    old_snapshot = LoadJson(old_fn)
    count = 0
    for key in old_snapshot.keys(): 
        if key in new_snapshot.keys():
            count += 1
            del new_snapshot[key]  
        checksumed_key = web3.toChecksumAddress(key)  
        if checksumed_key in new_snapshot.keys():
            count += 1
            del new_snapshot[key]          
    print(f"deleted {count} addresses using {old_fn}")
    return new_snapshot

def get_renbtc_mint(out_file_name=None):
    mints = Counter()
    # block number the gateway contract got deployed 
    # https://etherscan.io/tx/0x697063909e68c0f9230f6015aed0332de2bbf660ca44c19d19e7fd9888f4cf66
    START_BLOCK = 9737055   
    SNAPSHOT_BLOCK = 11285016 # Nov 19 00:00 
    BTC_GATEWAY_ADDRESS = "0xe4b679400F0f267212D5D812B95f58C83243EE71"
    renBTC = ContractLogParser(
                            startBlock=START_BLOCK,
                            endBlock=SNAPSHOT_BLOCK,
                            address=BTC_GATEWAY_ADDRESS,
                            abi_fn="./interfaces/Gateway.json",
                            event_name='LogMint',
                            )
    for log in renBTC.get_logs():
        # contract address that interacted with btcgateway
        contract_address = log['args']['_to']
        # skip the addresses that we can't decode 
        if contract_address in SKIP_ADDRESSES: continue
        # get transaction of the event to read the input data
        tx = web3.eth.getTransaction(log.transactionHash.hex())
        # checking skip addresses again, because sometimes log['args']['_to'] != tx.to
        if tx.to in SKIP_ADDRESSES: continue            
        # parse the user_address and amount
        result = getMintersInfo(tx)
        if result is None: continue
        user_address, amount = result
        mints[user_address] += amount

    result = processCounter(mints)   
    if out_file_name != None:
        WriteJson(out_file_name, result)
    return result    


def get_sbtc_lps(out_file_name=None):
    lps = Counter()
    STARTBLOCK = 10276544  #contract deploy block https://etherscan.io/tx/0x2d47c4beb316cc6644d217340dc7defff4a360634c9bf7584e7476230d89c7d1
    SNAPSHOT_BLOCK = 11285016  # Nov 19 00:00 UTC
    SBTC_LP_TOKEN_ADDRESS = '0x075b1bb99792c9E1041bA13afEf80C91a1e70fB3'
    SBTCLP = ContractLogParser(
                            startBlock=STARTBLOCK,
                            endBlock=SNAPSHOT_BLOCK,
                            address=SBTC_LP_TOKEN_ADDRESS,
                            abi_fn="./interfaces/CurveLP.json",
                            event_name='Transfer',
                            )  
    for log in SBTCLP.get_logs():                              
        sender = log.args._from
        receiver = log.args._to
        amount = log.args._value
        txid = log.transactionHash.hex()
        if receiver in SKIP_ADDRESSES: continue
        elif (receiver in CURVE_ADAPTERS) or (receiver in INSTACCOUNT) or (receiver in ARGENT):
            tx = web3.eth.getTransaction(txid)
            result = getMintersInfo(tx)
            if result is None: continue
            user_address, _ = result
            lps[user_address] += amount
        elif receiver in ZAPPER:
            tx = web3.eth.getTransaction(txid)
            result = getMintersInfo(tx)
            if result is None: continue
            try:
                user_address, amount = result
                lps[user_address] += amount 
            except Exception as e:
                pass 
        else:
            lps[receiver] += amount

    result = processCounter(lps)
    print(len(result))   
    if out_file_name != None:
        WriteJson(out_file_name, result)
    return result     


def get_renbtc_lps(out_file_name=None):
    lps = Counter()
    STARTBLOCK = 10151366   #contract deploy block https://etherscan.io/tx/0x2edb903a20284a074eb3a5140ed79071e1ad8d0a4926dc176bef2bfecc388604
    SNAPSHOT_BLOCK = 11285016  # Nov 19 00:00 UTC
    CURVE_RENBTC_LP_ADDRESS = '0x49849C98ae39Fff122806C06791Fa73784FB3675'
    renBTCLP = ContractLogParser(
                            startBlock=STARTBLOCK,
                            endBlock=SNAPSHOT_BLOCK,
                            address=CURVE_RENBTC_LP_ADDRESS,
                            abi_fn="./interfaces/CurveLP.json",
                            event_name='Transfer',
                            )  
    for log in renBTCLP.get_logs():                              
        sender = log.args._from
        receiver = log.args._to
        amount = log.args._value
        txid = log.transactionHash.hex()
        if receiver in SKIP_ADDRESSES: continue
        elif (receiver in CURVE_ADAPTERS) or (receiver in INSTACCOUNT) or (receiver in ARGENT):
            tx = web3.eth.getTransaction(txid)
            result = getMintersInfo(tx)
            if result is None: continue
            user_address, _ = result
            lps[user_address] += amount
        elif receiver in ZAPPER:
            tx = web3.eth.getTransaction(txid)
            result = getMintersInfo(tx)
            if result is None: continue
            try:
                user_address, amount = result
                lps[user_address] += amount 
            except Exception as e:
                pass 
        else:
            lps[receiver] += amount

    result = processCounter(lps)
    print(len(result))   
    if out_file_name != None:
        WriteJson(out_file_name, result)
    return result     


def get_uniswap_lps(out_file_name=None):
    suppliers = Counter()

    UNISWAP_WBTC_ETH_LP_ADDRESS = '0xBb2b8038a1640196FbE3e38816F3e67Cba72D940'
    WBTC_ADDRESS = '0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599'
    
    uniWBTCETHABI = LoadJson(f"./interfaces/UniswapPair.json")
    ERC20_ABI = LoadJson(f"./interfaces/ERC20.json")
    START_BLOCK = 9737055
    UNISWAP_SNAPSHOT_BLOCK = 11304643 #Nov-22-2020 12:00:00 AM +UTC

    uniswap = web3.eth.contract(UNISWAP_WBTC_ETH_LP_ADDRESS, abi=uniWBTCETHABI)
    wbtc = web3.eth.contract(WBTC_ADDRESS, abi=ERC20_ABI)

    for start in trange(START_BLOCK, UNISWAP_SNAPSHOT_BLOCK, 1000):
        end = min(start + 999, UNISWAP_SNAPSHOT_BLOCK)
        logs = uniswap.events.Transfer().getLogs(fromBlock=start, toBlock=end,argument_filters={"from": ZERO_ADDRESS})
        wbtc_logs = wbtc.events.Transfer().getLogs(fromBlock=start, toBlock=end,argument_filters={"dst": UNISWAP_WBTC_ETH_LP_ADDRESS})
        for log in logs:
            if log['args']['from'] == ZERO_ADDRESS:
                lp_provider = log.args.to
                mint_txid = log.transactionHash
                if lp_provider in UNI_UNDECODABLE: continue 
                want_log = next(
                                filter( lambda tx: (tx.transactionHash == mint_txid) and\
                                                    (tx.args.dst == UNISWAP_WBTC_ETH_LP_ADDRESS), wbtc_logs))

                if (lp_provider in ARGENT_UNISWAP) or (lp_provider in ZAPPER):
                    tx = web3.eth.getTransaction(mint_txid)
                    result = getMintersInfo(tx)
                    if result is None: continue
                    user_address, _ = result
                    suppliers[user_address] += want_log.args.wad
                elif lp_provider in ZERION:
                    tx = web3.eth.getTransaction(mint_txid)
                    suppliers[tx["from"]] += want_log.args.wad
                else:               
                    suppliers[lp_provider] += want_log.args.wad

    result = processCounter(suppliers)
    if out_file_name != None:
        WriteJson(out_file_name, result)
    return result     


@cached('snapshot/08-merkle-distribution.json')
def step_07(balances):
    elements = [(index, account, amount) for index, (account, amount) in enumerate(balances.items())]
    nodes = [encode_hex(encode_abi_packed(['uint', 'address', 'uint'], el)) for el in elements]
    tree = MerkleTree(nodes)
    distribution = {
        'merkleRoot': encode_hex(tree.root),
        'tokenTotal': hex(sum(balances.values())),
        'claims': {
            user: {'index': index, 'amount': hex(amount), 'proof': tree.get_proof(nodes[index])}
            for index, user, amount in elements
        },
    }
    print(f'merkle root: {encode_hex(tree.root)}')
    return distribution


def deploy():
    user = accounts[0] if rpc.is_active() else accounts.load(input('account: '))
    tree = json.load(open('snapshot/07-merkle-distribution.json'))
    root = tree['merkleRoot']
    token = str(DAI)
    MerkleDistributor.deploy(token, root, {'from': user})


def writeCsv(out_file_name, items):
    with open(out_file_name, mode='w') as fp:
        csv_writer = csv.writer(fp, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        csv_writer.writerow(["Address", "amount"])
        for addr, amount in items:
            csv_writer.writerow([addr, amount])

def main():

    print("#5 - yearn snapshot and ygov Governance")
    # yearn = get_ygov_and_snapshot_participants(out_file_name="./snapshot/yearn.json") 
    # yearn = LoadJson("./snapshot/yearn.json")   
    # writeCsv("./snapshot/yearn.csv", yearn.items()) 

    print("#8 - Minted renBTC")
    # renbtc_mints = get_renbtc_mint(out_file_name="./snapshot/renbtc_mint.json")    
    # renbtc_mints = cleanupSnapshot(renbtc_mints, './old_snapshot/renbtcMinters.json')
    # old_renbtc_mints = LoadJson("./old_snapshot/renbtcMinters.json")
    # writeCsv("./snapshot/old_renbtc_mint.csv", old_renbtc_mints.items())
    # fix dupilcates error
    renbtc_mints = LoadJson("./snapshot/renbtc_mint.json")
    renbtc_mints = cleanupSnapshot(renbtc_mints, './old_snapshot/renbtcMinters.json')
    writeCsv("./snapshot/renbtc_mint.csv", renbtc_mints.items())

    print("#10 - Curve SBTC LPs")
    # curve_sbtc_lp = get_sbtc_lps(out_file_name="./snapshot/curve_sbtclp.json")
    # curve_sbtc_lp = cleanupSnapshot(curve_sbtc_lp, './old_snapshot/sbtcLP.json')
    # curve_sbtc_lp = LoadJson("./old_snapshot/sbtcLP.json")
    # writeCsv("./snapshot/old_curve_sbtclp.csv", curve_sbtc_lp.items())
    # fix dupilcates error
    curve_sbtc_lp = LoadJson("./snapshot/curve_sbtclp.json")
    curve_sbtc_lp = cleanupSnapshot(curve_sbtc_lp, './old_snapshot/sbtcLP.json')
    writeCsv("./snapshot/curve_sbtclp.csv", curve_sbtc_lp.items())

    print("#10 - Curve renBTC  LPs")
    # curve_renbtc_lp = get_renbtc_lps(out_file_name="./snapshot/curve_renbtclp.json")    
    # curve_renbtc_lp = cleanupSnapshot(curve_renbtc_lp, './old_snapshot/renbtcLP.json')
    # curve_renbtc_lp = LoadJson("./old_snapshot/renbtcLP.json")
    # writeCsv("./snapshot/old_curve_renbtclp.csv", curve_renbtc_lp.items())
    # fix dupilcates error
    curve_renbtc_lp = LoadJson("./snapshot/curve_renbtclp.json")
    curve_renbtc_lp = cleanupSnapshot(curve_renbtc_lp, './old_snapshot/renbtcLP.json')
    writeCsv("./snapshot/curve_renbtclp.csv", curve_renbtc_lp.items())

    print("#16 - Provided wBTC/ETH liquidity on Uniswap ")
    # uniswap = get_uniswap_lps(out_file_name="./snapshot/uniswap.json")
    # uniswap = cleanupSnapshot(uniswap, './old_snapshot/uniLP.json')
    # uniswap = LoadJson("./old_snapshot/uniLP.json")
    # writeCsv("./snapshot/old_uniswap.csv", uniswap.items())
    # fix dupilcates error
    uniswap = LoadJson("./snapshot/uniswap.json")
    uniswap = cleanupSnapshot(uniswap, './old_snapshot/uniLP.json')
    writeCsv("./snapshot/uniswap.csv", uniswap.items())

    print("exiting early")
    sys.exit(0)

    AIRDROP_AMOUNT = 12574850300000000000000
    final = Counter()
    grandTotal = 0
    total = 0
    check = 0
    #yearn = LoadJson("./snapshot/yearn.json")

    for key in yearn:
        total += yearn[key]

    for key in yearn:    
        yearn[key] =  Wei((yearn[key]/total)*AIRDROP_AMOUNT)
        check += yearn[key]
        final[web3.toChecksumAddress(key)] += yearn[key]
    
    print("yearn Governance:", Wei(check).to("ether"))

    AIRDROP_AMOUNT = 12574850300000000000000
    grandTotal += check
    total = 0
    check = 0
    #renbtc_mints = LoadJson("./snapshot/renbtc_mint.json")
    #renbtc_mints = cleanupSnapshot(renbtc_mints, './old_snapshot/renbtcMinters.json')
    #
    for key in renbtc_mints:
        total += renbtc_mints[key]

    for key in renbtc_mints:    
        renbtc_mints[key] =  Wei((renbtc_mints[key]/total)*AIRDROP_AMOUNT)
        check += renbtc_mints[key]
        final[web3.toChecksumAddress(key)] += renbtc_mints[key]
    
    print("Minted renBTC:", Wei(check).to("ether"))


    AIRDROP_AMOUNT = 12574850300000000000000
    grandTotal += check
    total = 0
    check = 0
    #curve_sbtc_lp = LoadJson("./snapshot/curve_sbtclp.json")
    #curve_sbtc_lp = cleanupSnapshot(curve_sbtc_lp, './old_snapshot/sbtcLP.json')
    #
    for key in curve_sbtc_lp:
        total += curve_sbtc_lp[key]

    for key in curve_sbtc_lp:    
        curve_sbtc_lp[key] =  Wei((curve_sbtc_lp[key]/total)*AIRDROP_AMOUNT)
        check += curve_sbtc_lp[key]
        final[web3.toChecksumAddress(key)] += curve_sbtc_lp[key]
    
    print("Curve SBTC LPs:", Wei(check).to("ether"))    


    AIRDROP_AMOUNT = 12574850300000000000000
    grandTotal += check
    total = 0
    check = 0
    #curve_renbtc_lp = LoadJson("./snapshot/curve_renbtclp.json")
    #curve_renbtc_lp = cleanupSnapshot(curve_renbtc_lp, './old_snapshot/renbtcLP.json')
    #
    for key in curve_renbtc_lp:
        total += curve_renbtc_lp[key]

    for key in curve_renbtc_lp:    
        curve_renbtc_lp[key] =  Wei((curve_renbtc_lp[key]/total)*AIRDROP_AMOUNT)
        check += curve_renbtc_lp[key]
        final[web3.toChecksumAddress(key)] += curve_renbtc_lp[key]
    
    print("Curve renBTC  LPs:", Wei(check).to("ether"))    


    AIRDROP_AMOUNT = 12574850300000000000000
    grandTotal += check
    total = 0
    check = 0
    #uniswap = LoadJson("./snapshot/uniswap.json")
    #uniswap = cleanupSnapshot(uniswap, './old_snapshot/uniLP.json')
    #
    for key in uniswap:
        total += uniswap[key]

    for key in uniswap:    
        uniswap[key] =  Wei((uniswap[key]/total)*AIRDROP_AMOUNT)
        check += uniswap[key]
        final[web3.toChecksumAddress(key)] += uniswap[key]
    
    print("Provided wBTC/ETH liquidity on Uniswap:", Wei(check).to("ether"))      

    grandTotal += check


    print("Total:", Wei(grandTotal).to("ether"))
    print("Missing:", Wei(2100000000000000000000000-grandTotal).to("ether"))

    final = smooth(final)    

    with open('./snapshot/final.json', 'w') as fp:
        json.dump(final, fp)
    step_07(final)    