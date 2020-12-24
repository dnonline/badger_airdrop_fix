## Usage
To generate the snapshot data:

```
pip install -r requirements.txt

brownie networks add Ethereum archive host=$YOUR_ARCHIVE_NODE chainid=1

brownie networks add Ethereum xdai host='https://dai.poa.network' chainid=100

brownie run renbtcsnapshot --network archive
```

## Notes
Used snapshot data and some code from https://github.com/andy8052/badger-merkle

